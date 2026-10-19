# Phase 5 — FE Resources Page (Slice 5, arch §4)

**Commission:** resource-cost-control · **Branch/worktree:** `feature/resource-cost-control` @ `/home/nea/ensemble-src-wt-resource-cost-control`
**Basis:** `architecture-recommendation.md` §4 (FE contract) + §2.8 (API surface — **FROZEN**). FE surface is **fixed by the user**; this file turns it into an implementer-ready plan. No redesign.
**Mode note:** the planning dir holds architecture docs but no committed plan set (`plan-overview.md`/`phaseN-plan.md` do not exist yet). Per dispatch, **exactly one file** (`phase5-plan.md`) is written; D#/T#/AC#/R# numbering below is **phase-local** and starts at 1.
**Parallelism:** the API contract is frozen (§2.8), so this phase is buildable **in parallel with P1–P4** — unit tests mock the service, and the e2e gate is daemon-free by design (D8).
**CURRENCY (2026-10-18 delta):** FE anchors re-verified read-only post-merge (worktree @ `8b8b32dcc`, fast-forward of `origin/latest` through 2026-10-18). (1) Live-view passcode `09d4c9490` = **NO route/menu collision** — `frontend/src/` has zero `passcode` hits, and `app.routes.ts` / `app.ts` contain no `resources` route; the passcode feature is server-side only, so the `/resources` lazy route + gear-menu append are unaffected. (2) db-backed settings `d0b475466` also touched FE — the settings page (`pages/settings/settings.component.ts/.html/.scss/.spec.ts`, landed via `f3a65e294`/`1283ad9a3`; `services/settings.service.ts` in place since the timezone-picker work) with the lazy `/settings` route at `app.routes.ts:50`: **no route conflict** with our lazy `/resources` (distinct top-level paths; no `resources` path exists anywhere in the route table). (3) The `settingsMenuItems` seed array is **unchanged at `app.ts:566-576`** — the anchor has NOT shifted: Snapshots remains the last static entry (`:575`), and the conditional Database/Maintenance appends still land at the menu tail (`:776`, `:820-821`), so T6's append-after-Snapshots insertion point stands exactly as written. FE design otherwise unchanged (fixed by the user).

---

## 1. Goal

Ship the `/resources` page: a gear-menu entry → lazy route → Maintenance-shell-style tabbed page with a **Live** tab (8 s gated poll of `/api/resources/live` + self-cost chip) and a **Timeline** tab (dependency-free inline SVG line/area from `/history`, anomaly markers from `/anomalies`, click-a-marker drill-down into `/anomalies/{id}/evidence` + `/traceback?t0=&t1=`) — colocated jest specs + a daemon-free Playwright gate — with the initial bundle untouched and all three FE gates green at the phase boundary.

**Viz escalation clause (fixed decision viz_v1):** the hand-rolled SVG is estimated at **~0.5–1.0 dev-day** — below the 2 dev-day threshold, so **no escalation to leader and no echarts**. If implementation evidence pushes the estimate past 2 dev-days, STOP and escalate (echarts is the named fallback candidate); do not silently add a dependency.

## 2. Deliverables (exact paths)

New files:

| Path | What |
|---|---|
| `frontend/src/app/models/resources.models.ts` | TS interfaces mirroring §2.8 payloads (`LivePayload`, `HistoryPoint`, `HistoryPayload`, `Anomaly`, `AnomaliesPayload`, `EvidencePayload`, `TracebackPayload`, `SelfCostPayload`) |
| `frontend/src/app/services/resources.service.ts` | Typed HTTP client: `getLive()`, `getHistory(window, resolution?)`, `getAnomalies(window?, limit?)`, `getEvidence(id)`, `getTraceback(t0, t1)`, `getSelfCost()` — same-origin `/api/resources/*`, no auth plumbing (D1) |
| `frontend/src/app/pages/resources/resources.component.ts` `.html` `.scss` | Tab shell cloned from `maintenance.component.ts` registry pattern (D3) |
| `frontend/src/app/pages/resources/live/live-tab.component.ts` `.html` `.scss` | Live tab: 8 s gated poll + skeleton/retain/error (D6, D7) + self-cost chip |
| `frontend/src/app/pages/resources/timeline/timeline-tab.component.ts` `.html` `.scss` | Timeline tab: history SVG + anomaly markers + marker-click drill-down |
| `frontend/src/app/pages/resources/svg-timeline/svg-timeline.component.ts` | Pure presentational dependency-free SVG primitive (line/area + time axis + markers + click emit). Inline template/styles acceptable |
| Colocated specs | `resources.component.spec.ts`, `live-tab.component.spec.ts`, `timeline-tab.component.spec.ts`, `svg-timeline.component.spec.ts`, `resources.service.spec.ts`, `resources-gear-menu.spec.ts` (all under `pages/resources/` except service spec, colocated beside its service) |
| `frontend/playwright.resources-gate.config.ts` | Clone of `playwright.snapshots-v2stub.config.ts` (D8), `testMatch` locked to the gate spec, default port **14202** (≥ 14201), env override `RESOURCES_GATE_PORT` |
| `frontend/e2e/resources-gate.spec.ts` | Daemon-free gate: `page.route` stubs for all six endpoints, asserts menu→page→tabs→live→timeline→marker drill-down→self-cost chip |

Edited files (minimal diffs):

| Path | Edit |
|---|---|
| `frontend/src/app/app.routes.ts` | +1 lazy route: `{ path: 'resources', loadComponent: () => import('./pages/resources/resources.component').then(m => m.ResourcesComponent), title: 'Resources' }` (same pattern as `snapshots` at `:62`, `maintenance` at `:83`) |
| `frontend/src/app/app.ts` | +1 static entry in the `settingsMenuItems` seed array (`:566-576`): `{ label: 'Resources', icon: 'insights', route: '/resources' }`, appended **after** Snapshots so the conditional Database/Maintenance appends (`:776`, `:820-821`) keep landing at the menu tail |

Explicitly **not** deliverables: no auth interceptor, no top-level header link, no chart library, no `frontend/design-tokens/` (D5), no Live-tab sparkline (out of scope — Live tab is stat cards; the SVG belongs to the Timeline tab), no `/control-capabilities` or `/profile` UI (that surface belongs to P6, not this fixed FE v1).

## 3. Decisions (FIXED — recorded, not re-opened)

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D1 | No auth plumbing — same-origin `/api` calls | House posture: app fully unauthenticated today; §2.8 ships the router same-origin/no-auth | Auth headers/interceptor; feature flag |
| D2 | Gear-menu **static append** to `settingsMenuItems` (`app.ts:566-576`) + lazy `loadComponent` route; **NOT** a top-level header link | Seed array precedent (Blueprints/MCP/Settings/Snapshots); conditional appends at `:776`/`:820` stay tail-loaded. Static is correct because the endpoint ships in the same promote train and the API **degrades to empty shapes, never 503** (§2.8) | Conditional availability check like Maintenance (unnecessary here); header nav link |
| D3 | Page shell = local section registry cloned from `maintenance.component.ts`: `@for (section of sections; track section.id)` + `*ngComponentOutlet` (`maintenance.component.html:9,15`); `mat-tab-group` lazy-mounts bodies so only the active tab instantiates | Verified registry pattern incl. disabled-placeholder option; lazy-mount gives tab gating for free (see D6) | Shared cross-page registry; hand-rolled tab bar; all-tabs-eager mount |
| D4 | Dependency-free hand-rolled inline SVG timeline (line/area + time axis + anomaly markers + click→window drill-down). **NO new npm chart dependency** | No chart lib exists in the repo (verified 3 ways); fixed user decision viz_v1 | echarts/any chart dep now (fallback only via the §1 escalation clause) |
| D5 | Styling: **raw slate hex** / global `styles.scss` fallback vars. Do **NOT** use the provably-inert `:root`-in-component-scss pattern. Do **NOT** create `frontend/design-tokens/` in this commission | Fixed user decision (tokens); global custom-property surface is minimal (verified), so raw hex is the practical primary device | Component-scoped `:root` tokens; new design-tokens dir (blueprint stale — finding logged §8) |
| D6 | Gated `setInterval` 8 s live tick, per `job-queue-indicator.component.ts` precedent (`POLL_INTERVAL_MS = 8000` at `:130`; `setInterval` in `ngOnInit` `:577`; `clearInterval` in `ngOnDestroy` `:582`) + skip-if-in-flight guard | Verified precedent; "gated" falls out of D3's Material lazy-mount (inactive tab body never mounts ⇒ its interval never starts) plus `ngOnDestroy` cleanup | RxJS timer/interval chains; SSE; polling while tab inactive |
| D7 | Loading/error shape per `jobs.component.html:355-395`: skeleton fires **only on first fetch**; background refreshes retain last-good data and never flash a skeleton; single error/empty branch with retry CTA | Verified template + classifier precedent | Per-widget spinners; toast-based errors; blank-on-error |
| D8 | e2e clones `playwright.snapshots-v2stub.config.ts`: `webServer` runs `ng serve` on an own port (default **14202**, ≥ 14201; env `RESOURCES_GATE_PORT`), **zero daemon/DB**, ALL API stubbed via `page.route`, `testMatch` locked to `resources-gate.spec.ts` only, workers 1 / retries 0 | Verified stub config (`frontend/playwright.snapshots-v2stub.config.ts`); jest already ignores `<rootDir>/e2e/` (`jest.config.js:4`) | Reusing the default `playwright` config (sweeps daemon-dependent specs); widening testMatch |
| D9 | FE consumes §2.8 endpoints **as-is**; models mirror the frozen payload shapes. Any perceived drift = escalate to leader, never reshape FE around a changed API | Contract frozen by dispatch | Defensive client-side normalization layers; FE-driven API redesign |
| D10 | Timestamps: treat API `t` / `t_start` / `t_end` as **naive-UTC** and parse with an explicit `Z` suffix before `Date` formatting | BE TZ hazard class (§2.6: naive-UTC spine vs aware `event.created_at`); prevents session-TZ skew in axis labels and drill-down windows | `new Date(str)` naive parse (local-TZ skew); FE-side TZ conversion UI (out of scope v1) |

## 4. Task breakdown

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|----------------|----|
| T1 | Models + service: write §2.8-mirroring interfaces and `ResourcesService` (six methods, typed params: `window ∈ {15m,1h,24h,7d}`, `resolution ∈ {auto,raw,minute,hour}`); jest spec asserting URL/query-string construction and payload typing via `HttpTestingController` | `npm test` green incl. `resources.service.spec.ts` | `models/resources.models.ts`, `services/resources.service.ts` (+spec) | — | R5 | AC1, AC2 |
| T2 | SVG primitive `svg-timeline`: fixed `viewBox` (e.g. 1000×320, `preserveAspectRatio="none"` avoided for text — use separate axis overlay), plain-function x/y scales, area+line `<path>` per enabled series (daemon_rss_mb, cgroup_mem_mb, daemon_cpu default on; threads/instances_active toggles), time-axis ticks (UTC, D10), anomaly marker `<circle>`s positioned by `t`, `(title)` native tooltips, `markerClick` output emitting `{anomaly, t0, t1}` with `t0 = t_start − pad`, `t1 = t_end + pad` (pad = max(60 s, 10 % window)); jest spec with fixture points asserting path `d` non-empty, marker cx positions, click payload math, UTC tick labels | spec green; no external imports beyond `@angular/*` | `pages/resources/svg-timeline/svg-timeline.component.ts` (+spec) | T1 | R1, R3 | AC1, AC2, AC5 |
| T3 | Live tab: `POLL_INTERVAL_MS = 8000`; `forkJoin({ live, selfCost })` per tick (precedent D6); first-fetch skeleton → data; background failure retains last data and flips the single error branch (retry CTA re-kicks immediately); skip tick if previous in flight; stat cards for `daemon.{pid,cpu_pct,rss_mb,threads,fds,uptime_s}`, `cgroup.memory.*`, `attribution.{attributed_mb,unattributed_mb,coverage_pct}` (label `estimated` honestly per §2.2), `overruns.{count,last_ts}`; **self-cost chip**: `cpu_pct`, `cumulative_cpu_s`, `buffers_bytes→MB`, `threads`, `samples`, `ticks_skipped`; jest spec with fake timers proving: 8 s cadence, clear on destroy, skeleton-only-first-fetch, retain-on-error | spec green incl. fake-timer cadence test | `pages/resources/live/*` (+spec) | T1 | R4 | AC1, AC2, AC4, AC6 |
| T4 | Timeline tab: window selector (`15m│1h│24h│7d`, default `1h`), `forkJoin({ history, anomalies })` on mount/window change; render `svg-timeline` from `points`; markers from `anomalies`; marker click → `forkJoin({ evidence, traceback })` with the primitive's `t0/t1` (D10) → drill-down panel below the chart (anomaly summary: `kind, shape, severity, baseline vs observed`; contributors list with `share_pct`; traceback structural: alive instances, in-flight tasks, spawned children (`kind` highlighted — `chart_render` is the 8.3 GB-class signal); loading/error states per D7; jest spec: fixture-driven render + click fetch contract | spec green | `pages/resources/timeline/*` (+spec) | T1, T2 | R1, R3 | AC1, AC2, AC5 |
| T5 | Shell page: `ResourcesSection` registry `{ id, label, component }` with entries `live` → `LiveTabComponent`, `timeline` → `TimelineTabComponent`; template clones `maintenance.component.html` `@for` + `*ngComponentOutlet`; page-level `.scss` uses raw slate hex (D5); spec asserting both tabs render from the registry and that adding a tab is a one-line append | spec green | `pages/resources/resources.component.*` (+spec) | T3, T4 | R1 | AC1, AC2, AC7 |
| T6 | Route + menu: lazy route in `app.routes.ts` (D2 pattern); static `settingsMenuItems` append after Snapshots (`app.ts:566-576`); `resources-gear-menu.spec.ts` proving the seed array contains `{route:'/resources'}`, the route config is lazy (`loadComponent`), and no top-level header link was added | spec green | `app.routes.ts`, `app.ts` (+spec) | T5 | R5, R6 | AC1, AC2, AC7 |
| T7 | Playwright gate: `playwright.resources-gate.config.ts` (clone per D8, port 14202 default, `testMatch: /resources-gate\.spec\.ts/`) + `e2e/resources-gate.spec.ts` stubbing all six endpoints with fixture JSON (`page.route`); asserts: gear menu shows "Resources" → click navigates → two tabs → Live shows stubbed RSS + self-cost chip → Timeline renders ≥1 SVG path + stubbed anomaly marker → marker click shows evidence + traceback panel → all without any daemon; optional: one-line addition to `frontend/e2e/README.md` | `npx playwright test --config=playwright.resources-gate.config.ts` exits 0 | `playwright.resources-gate.config.ts`, `e2e/resources-gate.spec.ts` | T6 | R7 | AC1, AC8 |
| T8 | Boundary sweep: **npm-install precondition — `frontend/node_modules` must be present BEFORE the gates (run `npm install`/`npm ci` from `frontend/` if the worktree's `frontend/` lacks them — pinned convention: never bare `npx tsc`/`tsc` outside the package dir)**; then run all three FE gates in one pass, confirm `dist/` emits a separate lazy `resources` chunk and the **initial** bundle is within the 1 MB warning budget (`angular.json` budgets: initial `maximumWarning: 1MB`, `maximumError: 6MB`), worktree clean/committable — **GREEN boundary, no half-states** | three gates exit 0; chunk inventory recorded in the task report | build outputs only | T1–T7 | R5 | AC1, AC3, AC9 |

## 5. Acceptance criteria (testable)

Gate commands (exact — **NO lint/eslint exists in this repo; never claim a lint step**):

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json
cd frontend && npm test
cd frontend && npm run build
```

| ID | Criterion |
|----|-----------|
| AC1 | `./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` exits 0 (run from `frontend/`; never bare `tsc`/`npx tsc` outside the package dir — pinned convention) |
| AC2 | `npm test` (jest) exits 0 with all new specs passing: `resources.service.spec.ts`, `svg-timeline.component.spec.ts`, `live-tab.component.spec.ts`, `timeline-tab.component.spec.ts`, `resources.component.spec.ts`, `resources-gear-menu.spec.ts`; jest must NOT sweep `frontend/e2e/` (already excluded, `jest.config.js:4` — verify in run output) |
| AC3 | `npm run build` exits 0; `dist/` contains a **separate lazy chunk** for the resources page (not merged into `main`); initial budget unchanged: warning ≤ **1 MB** / error ≤ 6 MB (`angular.json` budgets) — the `/resources` route keeps its cost off the initial bundle |
| AC4 | Live tab semantics proven by jest (fake timers): fetch fires on init and every 8000 ms; `clearInterval` on destroy; skeleton renders only before first successful fetch; a failing background fetch retains last-good data and shows exactly one error branch with retry; retry re-kicks the tick; an in-flight tick is skipped, not queued |
| AC5 | Timeline semantics proven by jest: SVG renders line/area from `points` fixture; anomaly markers positioned by `t` under the UTC parse (D10); clicking a marker triggers exactly `getEvidence(id)` + `getTraceback(t0, t1)` with `t0/t1` derived from the anomaly window (pad rule); drill-down panel renders evidence contributors + traceback children |
| AC6 | **Self-cost budget criterion (explicit):** the Live tab's self-cost chip renders `{cpu_pct, cumulative_cpu_s, buffers_bytes, threads, samples, ticks_skipped}` from `GET /api/resources/self-cost` (§2.8), making a self-cost breach **observable in the UI**. The numeric budget itself (tick target < 5 ms, hard gate 50 ms, EMA ≤ 1 % of interval, memory floor < 20 MB — arch §2.8 "Self-cost discipline") is **enforced by the Phase 1 BE regression test** — this phase's FE criterion is display/visibility only, and must not re-implement or re-assert the budget numerically |
| AC7 | Integration: `settingsMenuItems` contains the Resources entry (gear menu, appended after Snapshots; conditional Maintenance/Database appends still land at the tail); navigating `/resources` lazy-loads the shell; the page renders its two tabs from the local registry via `ngComponentOutlet`; **no top-level header link exists** |
| AC8 | `npx playwright test --config=playwright.resources-gate.config.ts` (from `frontend/`) exits 0 with **zero daemon running** (all `/api/resources/*` served by `page.route` stubs); config's `testMatch` is locked to `resources-gate.spec.ts` |
| AC9 | Boundary green: AC1–AC3 all pass in a single pass at the phase boundary with no half-implemented tab left in the registry (if a tab must land late, it uses the maintenance-style `disabled: true` placeholder pattern — never a broken tab) |

## 6. Risks + rollback

| ID | Risk | Impact | Mitigation | Rollback |
|----|------|--------|------------|----------|
| R1 | SVG scope creep (zoom/brush/tooltips/custom gradients) | Medium — schedule slip toward the echarts escalation threshold | v1 shape is closed: line/area + time axis + markers + click drill-down (§1). Anything beyond = out of scope; the §1 escalation clause governs | Drop the `timeline` entry from the registry (one line) — page ships Live-only, still green |
| R2 | FE merges before P1–P4 ⇒ `/api/resources/*` 404s ⇒ page shows its error branch | Low — cosmetic, degrades cleanly | House posture is degrade-never-block (D7 single error branch); **coordination note:** prefer landing this phase in the same promote train as P1 so endpoints exist at promote | Revert the route+menu commit (two small edits) to hide the entry until BE lands |
| R3 | Naive-UTC misparse skews axis labels / drill-down windows (session TZ ±h) | Medium — wrong forensic windows, silent | D10 explicit-`Z` parse + T2/T4 jest assertions on UTC-derived labels and `t0/t1` math | Contained in `svg-timeline` + tab components; single-function fix |
| R4 | Slow/hung endpoint piles up ticks or blanks the UI | Low | D6 skip-if-in-flight guard + D7 retain-last-data; 8 s cadence is gentle (§2.8 API is cheap reads) | Guard is one `if`; revert-friendly |
| R5 | Bundle-budget regression via accidental eager import (e.g., page imported directly in `app.ts` instead of route `loadComponent`, or models file pulling a heavy barrel) | Medium — breaks AC3 and the fixed budget decision | T8 checks the `dist/` chunk inventory; routes/menu edits are the only `app.*` touches; models file stays dependency-free | Fix import graph; worst case revert route commit |
| R6 | Icon glyph (`insights`) missing from the loaded icon font | Low — cosmetic | Label carries the meaning; pick any classic-set glyph at implementation if the font lacks it; not gate-blocking | Swap icon string |
| R7 | e2e port collision (14202 occupied) | Low | Env override `RESOURCES_GATE_PORT`; pack convention re-picks the first free port in 14199..14210 without killing occupants (snapshots-v2stub precedent, config comment) | Re-run on another port |
| R8 | API contract drift discovered during implementation | Medium — temptation to reshape FE | D9: frozen §2.8; drift = escalate to leader with the concrete payload diff | n/a — stop-and-escalate |

**Rollback posture (whole phase):** every task is additive (new files + two small `app.*` edits). Boundary-green rule (AC9) means any failing task reverts to its predecessor commit and the phase still reports green at the last completed task. Full-phase rollback = drop the new files + revert the two edits; nothing outside `frontend/` is touched.

## 7. Dependencies

**Blocked by:** nothing. The API contract is frozen (§2.8); unit tests mock `ResourcesService`; the e2e gate is daemon-free (D8). This phase runs **in parallel with P1–P4** (and P6–P8).

**Blocks:** nothing hard. Soft links:
- **P8 (docs):** will reference this page; deliverable list above is the citation source.
- **P6 (control seam):** `/control-capabilities` + `/profile` UI is **future FE work, owner TBD** — deliberately excluded from this phase's fixed surface.
- **Promote train:** merge-order preference with P1 (see R2) — same-train landing avoids shipping a menu entry that 404s.

**Cross-phase conventions this phase must honor:** forkJoin-of-two per tick (`live`+`self-cost`; `history`+`anomalies`) per the 8 s forkJoin tick precedent; `estimated`/`unattributed`/`coverage_pct` honesty labels surfaced in UI copy (arch §2.2 — attribution is partial by physics; do not paper over).

## 8. Assumptions & open questions

- **A1 (assumption):** `ResourceMonitorConfig.enabled` defaults `true` (§2.9), so the menu entry needs no availability probe (D2). If the leader later flips the default to disabled-by-default, a conditional append (Maintenance pattern, `app.ts:820-821`) is the one-line retrofit — noted, not planned.
- **A2 (assumption):** §2.8 payload shapes are literal (field names/casing as written). Models are authored from §2.8 verbatim; any BE naming deviation is R8/escalation, not silent FE normalization.
- **A3 (finding to report, not to fix here):** the Core-Architecture blueprint names `frontend/design-tokens/` as canonical, but that location does not exist and must **not** be created in this commission (D5) — blueprint stale. Reported separately to the dispatcher; implementers should ignore that blueprint line.
- **Q1 (open, leader):** anomaly `window` param values for `/anomalies` — §2.8 shows `?window=&limit=` without an enum; plan assumes the same `{15m,1h,24h,7d}` family as `/history` (T4 uses the tab's selected window). Confirm or correct at implementation.
- **Q2 (open, leader):** default Timeline window `1h` at `resolution=auto` — auto-fall behavior (window > raw retention ⇒ minute/hour) is BE-side per §2.8; FE passes `resolution=auto` and renders `resolution_used`. No FE fallback logic. Confirm no FE-side truncation banner is required beyond showing `truncated` when `true` (plan: show a small "truncated" hint — one conditional, included in T4).
