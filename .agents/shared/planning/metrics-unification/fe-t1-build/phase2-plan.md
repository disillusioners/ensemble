# Phase 2: Tab manifest + 7-tab shell + `?tab=` deep links

## Objective

Create the FE-local typed **tab manifest** and rewire the `/metrics` shell
around it: 7 module tabs (6 central + 1 custom), a frame-level family-catalog
drawer (replacing the Families tab), and `?tab=` deep links that read and
write the query parameter.

The manifest is the config promise: **adding a series to a wired module is
registration-only**; only a new *tab* costs an FE commit.

This phase also establishes the **per-source state model** (AM-1) and the
**manifest type** that can express all four module dispositions (AM-8).

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 2.1 | Create `frontend/src/app/pages/metrics/tabs.manifest.ts` — the FE-local typed manifest. **The type must express all FOUR dispositions (AM-8):** HIDDEN (a source with **no** registered family — LLM tokens, LLM latency, source breakers), PLANNED-unbound-but-registered (F7, F18, F21, F0.3), BOUND-but-dark (F1, F3, F6), and DROPPED (never planned). Concretely: widen the union with a **third variant** (or a `source: 'none' \| 'registered' \| 'planned'` discriminant) and make `familyIds` **optional** (or add a `noFamily` variant) so a non-family module is type-expressible **without an empty-array sentinel** — "registered but no series yet" and "not instrumented" are otherwise type-indistinguishable. Everything derivable from the wire (`chart_role`, `role`, labels, availability, buckets) is read at runtime and **never duplicated** | Phase 1 | A new spec pins the manifest shape: 7 `live` tabs; unique, stable `id` slugs; every disposition is representable; a spec asserts each HIDDEN module binds to the no-family variant (not an empty `familyIds`) |
| 2.2 | Populate the 7 entries per `plan-overview.md` §4 — including the **bracketed planned families**: Runtime binds `[F1, F3]`, DB Pool binds `[F6]`. They are bound **now** and gated by availability, so R-ii / R-i land with **zero FE change** (ADJ-vi). Each `central` entry also declares its **stat-role families** for the per-tab stat strip | 2.1 | The manifest spec asserts the exact family binding per tab; a spec asserts the bracketed families are bound and currently dark |
| 2.3 | Populate the **planned (hidden) entries** for the no-source modules: LLM token usage (F14), LLM request latency (F15), source circuit-breakers/token-buckets — each with a `trackingNote` naming the gap and its owner, bound to the **no-family variant** (AM-8). They render **nothing** | 2.1 | The spec asserts no planned entry contributes a tab, a panel, or a route |
| 2.4 | Create the 7 tab bodies under `frontend/src/app/pages/metrics/tabs/` — one component per module tab. The 6 central tabs are thin: they **bind their families, request their series through the existing `MetricsService`, and surface their availability** (live panels, or the family's reason when dark — ADJ-vi). **SPLIT (C-2):** the registry hand-off and the per-kind+unit-group **panel composition** MOVE to Phase 4's task **4.15** — the registry is created only at 4.8, so a Phase-2 requirement for it made the phase-2 build uncompilable. Tab 1 (Resources) is the `custom` slot and is built in Phase 5 — Phase 2 lands its **manifest entry + shell wiring** with the body mounting the relocated module once Phase 5 lands (the tab is present and correct in the manifest; the body may render a "module landing here" placeholder **only until Phase 5 completes** — that placeholder is removed in Phase 5 and never reaches a release) | 2.1, 2.2 | The shell renders 7 tabs; **each of the 6 central tabs fetches its bound families and surfaces their availability** (live panels, or the family's reason when dark) — **no central tab is an empty shell** (W-10); the panel-composition assertions move to 4.15 |
| 2.5 | Rewire `frontend/src/app/pages/metrics/metrics.component.ts` — the `sections` registry (`:84-88`) is replaced by the manifest-driven tab list; `activeTabId` (`:118`) is now driven by the manifest ids; `onTabChange` (`:204-209`) writes `?tab=<id>` | 2.1 | The shell spec asserts the rendered tab labels and ids match the manifest exactly |
| 2.6 | Implement the deep-link read in `ngOnInit` (`:199-201`): read `queryParamMap`'s `tab`; a **known** id selects that tab; an **unknown/legacy** id (e.g. the old `overview` / `charts` / `families`) falls back to the default tab **without a redirect loop**; no `tab` param ⇒ the default tab (tab 1, Resources) | 2.5 | The shell spec asserts all four cases (known, unknown, legacy, absent); a legacy `?tab=overview` lands on tab 1 with **no** navigation storm |
| 2.7 | Move the family catalog from a tab to a **frame-level drawer** in the metrics shell: the drawer is opened from the frame toolbar, renders the existing catalog content (the `families-tab.component.ts` row rendering + `formatAvailabilityReason`, `metrics.model.ts:294-313`), and adds the missing affordances the audit found: sortable/filterable columns, `aria-sort` + click on the `<th>` cells (today inert — `families-tab.component.ts:50-55`), and the **error-gated** empty copy ("No families are registered." must not render under a failed load) | 2.5 | The drawer spec asserts the three new affordances; the error-gated copy has a planted-violation test (a failed catalog load ⇒ the error state, never "No families are registered.") |
| 2.8 | Remove the **dead `active` input** (`families-tab.component.ts:210` — declared, documented, passed by the shell at `metrics.component.ts:188`, never referenced; it implies a poll gate that does not exist) together with its shell plumbing. **The residue check is pinned (AM-23):** the exact command is `grep -rn "\bactive\b" frontend/src/app/pages/metrics/families/ frontend/src/app/pages/metrics/metrics.component.ts` with the allowlist row `families-tab.component.ts:210` (the declaration being removed) and **no** `:(exclude)` pathspec needed — every other hit must be zero | 2.7 | The pinned grep returns **zero** hits outside the allowlist row; the catalog component no longer accepts the input |
| 2.9 | Update `frontend/src/app/pages/metrics/metrics.component.spec.ts` (519 L) and `frontend/src/app/pages/metrics/metrics-gear-menu.spec.ts` to the new shape; **preserve** every existing behavioural assertion that still applies (the registry-degraded badge, the truncated-catalog hint, the empty-panel conditions at `:146-155`, the error copy at `:245`) and re-point the ones that describe the retired 3-tab shape | 2.5, 2.7 | The specs are green; a diff review shows no assertion silently deleted — each removal is either re-pointed or explicitly listed with a reason |
| 2.10 | **Add the client-side door timeout (AM-4).** `MetricsService` has **no** rxjs timeout today (`frontend/src/app/services/metrics.service.ts:47-87`) — a hung read leaves the page in the loading state with **no state at all**. Add the house-pattern `timeout(...)` operator (`frontend/src/app/services/api.service.ts:366-367` — `timeout(ApiService.SEND_MESSAGE_TIMEOUT_MS)` + a typed `TimeoutError` mapping) at ~15 s, mapping the expiry to the new **`timeout`** door class (no Retry — the read may still be in flight server-side) | 2.4 | A spec asserts the timeout fires and maps to the `timeout` class (a fakeAsync/clock test, not a real 15 s wait); the existing service specs stay green |
| 2.11 | **Establish the PER-SOURCE state model (AM-1).** The frame holds **per-tab** state, not one global state; the `central \| custom` slot is the seam. A `custom` slot renders **its own source's** state; a `central` slot renders the **central door's** state. The state object per tab carries `{ doorState, families, series, lastGood, errorClass }` | 2.4 | A spec asserts that a failing central source does **not** blank a custom tab's state, and that a failing custom source does not blank a central tab's state |
| 2.12 | **Pin the custom tab's window vocabulary to the frozen union (AM-19).** The manifest's `allowedWindows` for the custom slot must be a **subset of** the frozen `ResourcesWindow` union (`frontend/src/app/models/resources.models.ts:16` — `'15m' \| '1h' \| '24h' \| '7d'`; the door 400s anything else — `daemon/routers/resources.py:120-130`). The subset relation is enforced **at compile time** (a typed `ResourcesWindow[]`) or by a spec — so drift fails the build, not the only live-lit tab | 2.2 | The spec asserts `allowedWindows ⊆ ResourcesWindow`; a planted out-of-union value fails the build/spec |
| 2.13 | **Copy precedence (AM-20).** The manifest may carry a per-tab **variant** of a frame state's copy, but never a second parallel copy source. The copy SSOT itself (one structured const per class) is Phase 8's work (IMPL-18); this task fixes the **precedence rule** and the manifest's role | 2.1 | The manifest spec asserts a per-tab variant is a **variant of** a known state key (a closed union), not free text |

## Coupling

- **Tight with:** Phase 3 (the manifest's `allowedWindows`/`defaultWindowS` ARE
  the frame's range options — the frame must not own a second list), Phase 4
  (the registry + the per-tab panel composition arrive there — **C-2**: 2.4 is
  split so the phase-2 build compiles), Phase 5 (tab 1's custom slot), Phase 7
  (the redirect target id must equal the manifest entry's id: `resource`), Phase 8
  (the per-source empty state lives in the shell), Phase 9 (the door timeout's
  `timeout` class).
- **Loose with:** Phase 9 (the pixel list exercises the 7 tabs).
- **Independent of:** Phase 6. (Phase 1 is a **dependency** of 2.1 — its
  `Depends On` cell says so; Phase 4 is now **tight**, not independent.)

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The manifest duplicates wire-derivable facts (roles, availability) and drifts | Medium — the honest drift class the house warns about | The manifest spec asserts the manifest carries **no** wire-derivable field; the runtime reads availability from the catalog projection |
| A tab renders a dark family as an empty chart | Medium | The per-family availability gate (ADJ-vi) is a Phase 2 acceptance, not a Phase 8 nicety |
| The deep-link write-back fights the tab group's own selection change | Medium | One owner of `activeTabId`: the manifest id is the single slug source; the spec asserts the URL and the selected tab never disagree |
| A frame-level state blanks the **only live-lit tab** (the AM-1 defect) | High | 2.11's per-source state model + the L4 verification leg |
| The manifest type cannot express a hidden module | High (it would ship a dark shell) | 2.1's four-disposition type requirement + 2.3's binding spec |
| The 519 L shell spec is churned into meaninglessness | Medium | 2.9 requires each deleted assertion to be listed with a reason — a silent deletion fails the exit criterion |
| A hung door read leaves the page with no state at all | Medium | 2.10's typed timeout → the `timeout` class |

## Exit Criterion

- `frontend/src/app/pages/metrics/tabs.manifest.ts` exists, is typed, and is
  covered by a new spec; **all four dispositions are type-expressible** and the
  HIDDEN modules bind to the no-family variant.
- The shell renders **7** tabs whose labels + ids match the manifest exactly;
  the old 3-tab shape is gone.
- `?tab=resource` selects tab 1; an unknown id falls back with **no** loop;
  the URL is written back on tab change.
- The family catalog is a frame-level drawer with sortable, `aria-sort`-bearing,
  error-gated content.
- The dead `active` input and its plumbing are gone (the **pinned** grep
  returns zero hits outside its allowlist row).
- The `MetricsService` has the ~15 s typed timeout and maps the expiry to the
  `timeout` class.
- The state model is **per source**: a failing central source does not blank a
  custom tab, and vice versa (2.11's spec).
- The custom tab's `allowedWindows` is proven a **subset** of the frozen
  `ResourcesWindow` union.
- **Each of the 6 central tabs fetches its bound families and surfaces their
  availability** (live panels, or the family's reason when dark — ADJ-vi);
  **no central tab is an empty shell** (W-10). The panel-composition
  assertions (a mixed-kind family ⇒ two panels) live in **Phase 4's task 4.15**
  and are re-asserted in Phase 4's exit (C-2).
- The existing metrics specs are green; no assertion was silently dropped.
- `agents/.prompt-artifacts.sha256` still verifies **161/161**.
- **E-1 re-derivation:** the only MEASURED figure this phase gates on is the
  **16-family census** the tab table is bound to — **re-derived at lane base**
  against the registry SSOT (`daemon/services/metrics_query/wiring/registry.py:64`
  + `wiring/adjudication.py:51-78`, read at `c26cec612`) and **re-derived again
  at this phase's freeze** (the registry file is re-read and the manifest's
  family bindings are re-checked against it). No other MEASURED figure gates
  this phase.
