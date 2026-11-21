# Phase 4: The FE Tool-Usage Metrics tab

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Phase owner: plan-worker (FE phase)
Status: Draft for the planner's review
Depends on: **Phase 1 BE** (the `/api/tool-usage/*` domain route + its door auth posture) — this phase CONSUMES the frozen contract, it makes ZERO daemon edits.
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-tool-metrics-p1` @ `0b5624dc9` (branch `feature/tool-usage-metrics-p1`).

---

## 0. Dispatch mode

**Mode 1 — Greenfield.** The directory `.agents/shared/planning/tool-usage-metrics-p1/` did not exist before this run; this file is the first artifact in it. No committed plan set is being revised.

---

## 1. Objective

Ship ONE new tab on the existing `/metrics` page — **`tool-usage`** — that makes layer-2 candidacy **OBVIOUS** for the Emperor: per project, tools sorted so high-frequency vs zero/near-zero jumps out; a per-agent breakdown view; a daily trend chart via the house SVG renderer. The tab is the **measurement surface** for the writing-block Hybrid Tool Architecture dispatcher tier (ranking key = schema token weight × call frequency; zero/near-zero usage = strongest candidates; per-agent allow-list intersection matters). It consumes the **frozen** `/api/tool-usage/*` domain door and adds **ZERO** daemon code, **ZERO** new npm deps, and **ZERO** new EventSource streams.

Outcome test: with auth ON, `?tab=tool-usage` renders a project selector, a tool-usage table sorted by 7-day frequency (zero/near-zero rows visually distinct), an expandable per-agent breakdown, and a trend chart (house-svg-trend) for the selected tool; with auth OFF (the LIVE posture), the tab renders the ONE fail-closed banner (no Retry, runbook pointer), never blanks, and never storms requests.

---

## 2. Scope

### In scope (frontend/** ONLY)

- `frontend/src/app/pages/metrics/tabs.manifest.ts` — the 9th tab entry (slot `custom`) + the `MetricsTabId` union (derived).
- `frontend/src/app/pages/metrics/tabs/tabs.components.ts` — the import + the map line (host component).
- `frontend/src/app/pages/metrics/tabs/tool-usage-tab-host.component.ts` (NEW) — the thin static host that dynamic-imports the heavy body (the lazy mechanism).
- `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts` (NEW) — the heavy body (component + template + styles).
- `frontend/src/app/services/tool-usage.service.ts` (NEW) — the domain-route client.
- `frontend/src/app/models/tool-usage.models.ts` (NEW) — the wire types.
- `frontend/src/app/pages/metrics/tabs.manifest.spec.ts` — amendments (8 → 9).
- `frontend/src/app/pages/metrics/metrics.tool-usage.spec.ts` (NEW) — the tab pack.
- `frontend/src/app/services/tool-usage.service.spec.ts` (NEW) — the service spec.
- `frontend/src/app/models/tool-usage.models.spec.ts` (NEW) — the models spec.
- `frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts` — extended (vendor-static-edge invariant stays GREEN).
- One Playwright geometry item (auth-off state).

### Out of scope (REJECT-grade if touched)

- **Any daemon file** — phase 1 owns `/api/tool-usage/*`. This phase makes ZERO daemon edits.
- **`bundle-baseline.json`** + **`metrics.bundle-guard.spec.ts` threshold pin** — OWNED by this phase **only as of the leader ruling R-08 (architect §6.2 / LEADER RULING)**: `threshold_B4b_route_fetch_set_lazy_only_bytes` 185,000 → 188,000, plus the spec-pin update and the tester gate re-measure instruction. Previously this file was INVARIANT (R-08: a breach was escalated, never silently re-baselined) — **that invariant is superseded BY THIS LEADER RULING ONLY**. No other bundle-baseline.json edits are permitted; the re-baseline covers the architect-proven ~1.0–1.3 kB static residue with ~2.2 kB margin. Static fallback REJECTED; defer-phase-4 remains the honest fallback only if the leader had refused (moot — ruled YES).
  Amended by architect pass @ fd254b335 — LEADER RULING R-08 (§6.2)
- **Other tabs' components** — no edits.
- **`metrics.service.ts`** (the central door) — untouched; the data door is a NEW domain service.
- **`package.json`** — zero new npm deps.

---

## 3. Binding rulings (restated — binding, not re-litigated)

The lane's ruling SSOT is the dispatcher's pre-rulings (recorded in the dispatch). The entries that bind this phase:

| Ruling | Application here |
|---|---|
| **Pre-ruling 1 — 9th manifest entry; slot choice is MINE** | Ruled **`slot: 'custom'`** (see §4 Ruling A). The tab binds NO central F-family (data comes from the domain door), so it cannot be a `MetricsCentralTab` (which requires `familyIds: NonEmpty<MetricsRegisteredFamilyId>`). Evidence: `tabs.manifest.ts` `MetricsCustomTab` (`:133-160`) carries no `familyIds` member and types `allowedWindows` as `NonEmpty<ResourcesWindow>`; `MetricsCentralTab` (`:120-132`) requires `familyIds: NonEmpty<MetricsRegisteredFamilyId>`. The type system forces `custom`. |
| **Pre-ruling 2 — data door = NEW domain route** | A NEW FE service `ToolUsageService` targeting `/api/tool-usage/*` (the `nest-reports.service.ts` pattern). `metrics.service.ts` (the central door) stays untouched. |
| **Pre-ruling 3 — B4b strategy: tab body ships LAZY** | First dynamically-imported tab in the map. Exact-cover compile guarantee (the `Record<MetricsTabId, Type<unknown>>` contract) stays INTACT — the map value is a thin static host; the heavy body is fetched on selection via the `app.ts:391` VCR dynamic-import precedent. "R-08 B4b re-baseline: RULED YES — threshold_B4b_route_fetch_set_lazy_only_bytes 185,000 → 188,000. Lazy tab body REMAINS PRIMARY (minimizes incremental fetch-set growth); the re-baseline covers the architect-proven ~1.0–1.3 kB static residue with ~2.2 kB margin. Static fallback REJECTED." Defer-phase-4 remains the honest fallback only if the leader had refused any re-baseline (moot — ruled YES). Fallback (only if lazy is proven infeasible — must argue from evidence, not preference): explicit leader re-baseline ruling per R-08; NEVER a silent threshold edit. |
| **Pre-ruling 4 — house SVG only** | `app-house-svg-renderer` with `variant="trend"` for the daily series; counters step-rendered; `chart-theme.ts` palette; ZERO new npm deps; zero new EventSource streams. |
| **Pre-ruling 5 — door-state reuse** | Reuse `MetricsDoorStateComponent` (the nest-reports tab pattern) for the auth-off 503 and the six reusable door classes; domain-local copy only for the two domain-only outcomes. |

### Ruling A — slot = `custom` (decided, not deferred)

**The tab's slot is `custom`.** Evidence:
1. `MetricsCentralTab` requires `familyIds: NonEmpty<MetricsRegisteredFamilyId>` (`tabs.manifest.ts:124-126`) — a central tab MUST bind at least one registered family. Tool-usage binds **none** (it is not a metrics family; its data is the domain door). There is no "empty familyIds" sentinel (`NonEmpty` forbids it).
2. `MetricsCustomTab` (`tabs.manifest.ts:133-160`) is the only slot shape with no `familyIds` member and its own window vocabulary (`ResourcesWindow`). The resource tab (`slot: 'custom'`, `id: 'resource'`) is the in-tree precedent.
3. Consequence: the custom-tab count in `tabs.manifest.spec.ts` moves from 1 → 2 and the central count from 7 → 8. This is a **spec amendment**, recorded in Task 1.

### Ruling B — label and id

Slug `tool-usage`, label `Tool Usage`. The slug matches the label (no internal inconsistency, unlike the old `anomalies` slot).

---

## 4. Frozen inherited contracts

### 4.1 The frozen API contract (dispatcher-pinned; BE owns it — this phase CONSUMES)

- `GET /api/tool-usage/summary?project_id=<optional, omit=all-projects>&days=7` → `{ generated_at, rows: [{ tool_name, tool_category, calls_24h, calls_7d, calls_total, agents: [{ agent_id, calls_7d }] }] }` — rows sortable client-side by frequency.
- `GET /api/tool-usage/daily?project_id=&tool_name=&days=` → `{ series: [{ day, calls }] }` — feeds the trend chart.
- Auth posture: REPLICATES the central metrics door verbatim (503 `AUTH_NOT_CONFIGURED` when auth is configured off; scope set `maintenance:read|metrics:read`) — same as `daemon/routers/nest_reports.py:94-114`.

**Where this plan breaks if the shape changed** (flag for the architect pass):
- `ToolUsageRow` / `ToolUsageAgent` / `ToolUsageSummary` / `ToolUsageDailySeries` in `models/tool-usage.models.ts` (Task 3) are typed against these field names; a rename forces a model + service edit.
- The `calls_7d` field is the **ranking key's frequency input** (ranking = schema token weight × call frequency). If the BE drops `calls_7d` in favour of a different window, the sort + the "zero/near-zero jumps out" affordance must be re-derived.
- The `agents: [{ agent_id, calls_7d }]` shape is the per-agent breakdown input. If it becomes a separate endpoint, the breakdown view needs a second fetch.
- The auth posture (503 `AUTH_NOT_CONFIGURED`) is assumed identical to the central door so `MetricsDoorStateComponent` copy applies. If the BE returns a different error code, the door-state mapping in Task 2 must change.

### 4.2 Frozen FE contracts (NEVER broken by this phase)

- **Exact-cover compile guarantee**: `METRICS_TAB_COMPONENTS: Readonly<Record<MetricsTabId, Type<unknown>>>` (`tabs/tabs.components.ts:13-26`) — one entry per tab, no extra, no missing; a missing/extra key is a **compile error**. The lazy mechanism preserves this: the map value is a thin static host `Type`, not a loader function. The host is the compile-time witness; the heavy body is runtime-loaded.
- **Manifest registration-only contract** (W4): a new tab costs one manifest entry + one map line + one body. No other typed-table edits unless a new metric NAME is introduced (none here — tool-usage is not a metrics family).
- **`bundle-baseline.json` threshold** (`threshold_B4b_route_fetch_set_lazy_only_bytes = 185000`) — **PREMISE CORRECTION (🔴 arithmetic, architect §6.2)**: "lazy keeps B4b within the frozen bound" is FALSE. Static residue into the route chunk = manifest entry ≈125 B + map line/import ≈60 B + host ≈0.8–1.1 kB min ≈ ~1.0–1.3 kB, versus 134 B headroom; ANY 9th-tab variant breaches 185,000 (even a zero-byte host breaches at ~185 B minimum); expected measured B4b under lazy ≈ 185,800–186,200 B. The lazy mechanism itself is FEASIBLE (confidence HIGH — B4b exclusion is BY CONSTRUCTION, proven from bundle-baseline.measure.js's own static-import-regex logic; dynamic targets never enter the fetch set; corroborated by the 206,857 B chart.js vendor chunk being absent from the 184,866 B B4b number; the heavy body ≈18–24 kB contributes ZERO B4b bytes). **The threshold is therefore re-baselined 185,000 → 188,000 by the leader ruling R-08 (architect §6.2 / LEADER RULING)** — this is a phase-4 task (bundle-baseline.json + metrics.bundle-guard.spec.ts pin + tester re-measure), not a silent edit. The static-only fallback (≈205–210 kB) is REJECTED.
  Amended by architect pass @ fd254b335 — §6.2 B4b arithmetic + LEADER RULING R-08
- **Zero new npm deps** (`package.json` untouched).
- **House SVG only** (`app-house-svg-renderer`, `chart-theme.ts` palette, `viewBox 900×260`, COUNTER-HONESTY step-never-diagonal, UTC-never-local via `toISOString`).
- **Zero new EventSource streams** (five sites hold streams; nothing calls `disconnect()`; we add none).

---

## 5. Tasks

| # | Task | Depends on | Acceptance |
|---|---|---|---|
| 1 | **Manifest**: the 9th entry (`tool-usage`, `slot: custom`) + the spec census amendments (8 → 9) | — | Manifest carries 9 tabs; ids/spec/census unions consistent; map type-check demands the new body |
| 2 | **The tab body** (host + heavy) — project selector, usage table (freq-sorted, zero/near-zero distinct), per-agent breakdown, trend chart (house-svg-trend), door-state (auth-off 503) | 1, 3 | Table renders rows sorted by `calls_7d` desc; zero/near-zero rows visually distinct; per-agent breakdown expands; trend chart draws the daily series; exactly ONE banner per failing source |
| 3 | **The domain client + the wire models** (`services/tool-usage.service.ts`, `models/tool-usage.models.ts`) | BE phase (the route) | Client sends `Accept: application/json`, 15 s deadline, supersedes stale responses, maps the route's error vocabulary honestly |
| 4 | **The lazy mechanism** (host component + dynamic import) | 2 | Host is the statically-imported `Type` in the map; heavy body is NOT in the static import closure; bundle guard B4b unchanged |
| 5 | **Register the body** (`tabs/tabs.components.ts`) | 4 | Map covers the manifest EXACTLY (compile error otherwise) |
| 6 | **The gate packs** + the bundle re-measure | 1–5 | FE pack green; bundle guard GREEN with re-measured numbers recorded against the frozen baseline |
| 7 | **The one pixel item** (Playwright geometry, auth-off) | 2, 3 | Geometry assertions recorded |

### Task 1 — The manifest

**File: `frontend/src/app/pages/metrics/tabs.manifest.ts`**

1. **The tab entry** — APPENDED to `METRICS_TABS` (after `nest-reports`):
   ```ts
   {
     disposition: 'bound',
     slot: 'custom',
     id: 'tool-usage',
     label: 'Tool Usage',
     // The custom module's OWN ladder — a subset of the frozen
     // `ResourcesWindow` union. 7d is the measurement window the
     // BE default (`days=7`) and the writing-block ranking use.
     allowedWindows: ['24h', '7d'],
     defaultWindowS: '7d',
   },
   ```
   **Why APPENDED and not inserted:** the tab list order is user-visible (`?tab=` deep link + tab strip); inserting would move shipped tabs' positions. The precedent entries are appended the same way (`nest-reports` appended after `mcp`).
   **Why `allowedWindows: ['24h', '7d']`:** the BE `days` param; `ResourcesWindow` is `'15m'|'1h'|'24h'|'7d'`. `7d` matches the frozen `days=7` default and the ~7-day measurement window in the commission; `24h` gives a short-horizon view. No central ladder (this is a custom tab).
2. **No `copyVariant`** on a custom tab — the custom slot has no `copyVariant` member (`MetricsCustomTab`, `:133-160`). Door-state copy is owned by the body (Task 2), not the manifest.
3. **The spec amendments** — `frontend/src/app/pages/metrics/tabs.manifest.spec.ts`:
   - `:70-72` `it('carries exactly 8 tabs')` → **9**.
   - `:77-86` the ids array → append `'tool-usage'`.
   - `:90-96` the labels array → append `'Tool Usage'`.
   - `:96-100` `it('exactly ONE tab is the custom slot …')` → **2 custom / 8 central** (Ruling A adds a second custom tab).
   - `:102-116` the family-roster map → **unchanged** (tool-usage is custom, carries no family; the map is over `CENTRAL_TABS` only).
   - `:117-125` any custom-slot count assertions → update to 2.
    - **`metricsCustomTab()` "exactly one custom" prose is now FALSE** — amend the docstring and pin that it returns the resource tab first (the second custom tab is type-legal; the constraint is prose/spec-count only). Add a pin asserting the returns-resource-first ordering.
    - **Pin the `metrics-range` snap**: entering `tool-usage` from a `15m`/`1h` frame snaps to `7d` (the measurement window); add an assertion for the snap.
    - One AOT dev-build pass after template edits (NG8002/NG0303 invisible to tsc/jest).
  Amended by architect pass @ fd254b335 — §6.1 spec amendments

### Task 2 — The tab body (host + heavy)

**File (host): `frontend/src/app/pages/metrics/tabs/tool-usage-tab-host.component.ts` (NEW)** — selector `app-metrics-tool-usage-tab-host`, standalone, `ChangeDetectionStrategy.OnPush`, **thin**: no heavy imports, no service imports.

The host's only job is to be the statically-imported `Type` that satisfies the exact-cover map, and to lazy-load the heavy body on mount, mirroring `app.ts:391` (chat root lazy-mount via VCR):

```ts
// host component (sketch — the plan pins the contract, not the bytes)
// FROZEN host pattern (architect §6.1): standalone, OnPush, statically imported,
// declares ALL FIVE shell inputs (the shell passes the same 5 to every tab;
// undeclared inputs THROW in dev / silently drop in prod).
export class ToolUsageTabHostComponent implements OnInit, OnDestroy {
  private readonly vcr = inject(ViewContainerRef);
  private ref: ComponentRef<unknown> | null = null;
  // ALL FIVE shell inputs — forwarded onto the created heavy ref.
  readonly state = input<unknown>(null);
  readonly onState = input<unknown>(null);
  readonly window = input<string>('7d');
  readonly baseline = input<unknown>(null);
  readonly onRangeChange = input<unknown>(null);
  // loading placeholder + visible chunk-error state with retry (never-blank).
  readonly loading = signal(true);
  readonly chunkError = signal<unknown>(null);
  async ngOnInit() {
    this.loading.set(true);
    this.chunkError.set(null);
    try {
      const { ToolUsageTabComponent } = await import('./tool-usage-tab.component');
      this.ref = this.vcr.createComponent(ToolUsageTabComponent);
      // forward inputs onto the created ref
      this.ref.setInput('state', this.state());
      this.ref.setInput('onState', this.onState());
      this.ref.setInput('window', this.window());
      this.ref.setInput('baseline', this.baseline());
      this.ref.setInput('onRangeChange', this.onRangeChange());
      this.ref.changeDetectorRef.detectChanges();   // explicit detectChanges (zoneless)
    } catch (e) {
      this.chunkError.set(e);   // visible chunk-error state with retry
    } finally {
      this.loading.set(false);
    }
  }
  retry() { void this.ngOnInit(); }   // re-selection / retry re-runs init; module cache makes remount near-instant
  ngOnDestroy() { this.ref?.destroy(); }
}
```
The host template carries a loading placeholder + a visible chunk-error state with a Retry button (re-selection re-runs init; `mat-tab` destroys deselected bodies, so polling stops — desired). The auth-off banner lives in the HEAVY body via `MetricsDoorStateComponent` (already in the route chunk via nest-reports — zero new vendor bytes); a host-side banner is impossible because auth posture is a per-request outcome.
  Amended by architect pass @ fd254b335 — §6.1 host pattern (five inputs + chunk-error retry)

The host's template is a single anchor `<ng-container #anchor></ng-container>` (or the VCR is the host itself). **No heavy import at module-evaluation time** — the `import(...)` is the only reference to `tool-usage-tab.component`, so the compiler emits it as a separate chunk. The bundle guard's vendor-static-edge invariant + B4b confirm it stays out of the route fetch set.

**File (heavy): `frontend/src/app/pages/metrics/tabs/tool-usage-tab.component.ts` (NEW)** — selector `app-metrics-tool-usage-tab`, standalone, `ChangeDetectionStrategy.OnPush`. This is where all the logic lives.

**The body's sections:**

| Section | Source | Transport |
|---|---|---|
| **A — project selector + window** | local UI state | none (drives the service calls) |
| **B — tool-usage table** | `/api/tool-usage/summary` | `ToolUsageService` |
| **C — per-agent breakdown** | row expansion → `row.agents` (already in the summary payload; no second fetch) | none |
| **D — trend chart** | `/api/tool-usage/daily` for the selected tool | `ToolUsageService` + `app-house-svg-renderer` (`variant="trend"`) |
| **E — door state** | the domain route's failure | `MetricsDoorStateComponent` (reuse) |

**Section B — the tool-usage table (the layer-2 candidacy surface):**
- Columns: tool name · category · calls 24h · calls 7d · calls total · agents (count) · "layer-2 candidate" affordance.
- **Sorted by `calls_7d` DESCENDING client-side** (the ranking input; the BE returns unsorted rows per the contract). Ties broken by `tool_name` asc (deterministic). Zero/near-zero rows (`calls_7d <= threshold`) are visually distinct (a CSS class on the row + a badge "≈0 calls/7d" + a "candidate" marker) so the layer-2 candidates jump out.
- The "near-zero" threshold is a **LOCAL constant** in the component (`NEAR_ZERO_CALLS_7D = 3` — a UI affordance, NOT a BE contract). Marked as an assumption (§9).
- Row click expands the per-agent breakdown (Section C) inline (one expansion at a time; clicking another collapses the first) — the `agents: [{ agent_id, calls_7d }]` already in the payload, so no second fetch.
- Empty state is **DISTINCT** from the error state (a 503 must not render an empty table; an empty result renders "no tool usage recorded in the window").

**Section D — the trend chart:**
- `app-house-svg-renderer` with `variant="trend"`, `series` built from `GET /api/tool-usage/daily` (one series per tool, OR one series for the selected tool — the plan picks **one series for the selected tool** to keep the chart legible; the selector drives which tool). `unit="calls"`, `ariaLabel="Tool usage, calls per day (UTC)"`.
- The daily endpoint's `series: [{ day, calls }]` maps to `ChartSeries` points (epoch ms from the `day` string via `toISOString`-disciplined UTC parsing — the house rule: UTC-never-local).
- The chart is **step-rendered** (COUNTER-HONESTY: step-never-diagonal) — the house renderer already enforces this; we pass the series as-is.

**Door-state discipline (single banner):**
- The domain half's failure renders via `MetricsDoorStateComponent` (`[code]="banner.code"`, `[onRetry]`, `testId`, `retryTestId`) — the nest-reports tab pattern (`nest-reports-tab.component.ts:159-164`).
- Mapping (the door's **frozen 10-class union** stays untouched; the two domain-only outcomes get **domain-local** copy — see Task 3):
  - 503 `AUTH_NOT_CONFIGURED` → `code="auth_not_configured"` (no Retry — `retryable:false`).
  - 403 `SCOPE_INSUFFICIENT` → `code="scope_insufficient"`.
  - 401 → `code="auth_required"`.
  - 400 → `code="bad_request"`.
  - client deadline / transport → `code="timeout"` / `code="network"`.
  - 404 (unknown tool on the daily endpoint) → **domain-local** copy ("this tool is no longer known (it may have been pruned)").
  - 503 `tool_usage_unavailable` (the tool-usage substrate is down) → **domain-local** copy ("the tool-usage store is unavailable on this daemon") + Retry (transient).
- **Exactly ONE banner** when the source fails; no Retry on auth-off.

**`data-test` hooks** (house convention): `tool-usage-project-select`, `tool-usage-window-select`, `tool-usage-table`, `tool-usage-row-<tool_name>`, `tool-usage-zero-badge`, `tool-usage-candidate-badge`, `tool-usage-agent-breakdown`, `tool-usage-trend`, `tool-usage-empty`, `tool-usage-error`, `tool-usage-retry`.

### Task 3 — The domain client + the wire models

**File: `frontend/src/app/services/tool-usage.service.ts` (NEW)** — mirror `nest-reports.service.ts`:
- `static readonly SUMMARY_URL = '/api/tool-usage/summary'`
- `static readonly DAILY_URL = '/api/tool-usage/daily'`
- `static readonly JSON_HEADERS = { Accept: 'application/json' }`
- `static readonly DOOR_TIMEOUT_MS = 15_000`
- `summary(options: { projectId?: string; days?: number })` → `Observable<ToolUsageSummary>`
- `daily(options: { projectId?: string; toolName: string; days?: number })` → `Observable<ToolUsageDailySeries>`
- `withDeadline` (rxjs `timeout` + `MetricsDoorTimeoutError`), `params` (forward only when set).
- Supersede tokens are the **caller's** (the body owns one monotonic token per request class — the summary and the daily series are separate classes).

**File: `frontend/src/app/models/tool-usage.models.ts` (NEW)** — wire types:
```ts
export interface ToolUsageAgent { readonly agent_id: string; readonly calls_7d: number; }
export interface ToolUsageRow {
  readonly tool_name: string;
  readonly tool_category: string;
  readonly calls_24h: number;
  readonly calls_7d: number;
  readonly calls_total: number;
  readonly agents: readonly ToolUsageAgent[];
}
export interface ToolUsageSummary {
  readonly generated_at: string;
  readonly rows: readonly ToolUsageRow[];
}
export interface ToolUsageDailyPoint { readonly day: string; readonly calls: number; }
export interface ToolUsageDailySeries { readonly series: readonly ToolUsageDailyPoint[]; }
```
Plus the **domain-local copy table** (the exhaustive mapping over the two domain-only outcomes: `unknown_tool` and `tool_usage_unavailable`) — the house "no silent fall-through" rule, asserted exhaustively by `tool-usage.models.spec.ts`.

### Task 4 — The lazy mechanism

**The mechanism (chosen): VCR dynamic import, mirroring `app.ts:391`.**

- The map value in `tabs.components.ts` is `ToolUsageTabHostComponent` (a `Type<unknown>`), so the `Record<MetricsTabId, Type<unknown>>` exact-cover contract is preserved **verbatim** — no type change, no loader-function value.
- The host is the only statically-imported symbol from the tool-usage lane in `tabs.components.ts`. The heavy body (`tool-usage-tab.component.ts`), the service, and the models are reached only via `await import('./tool-usage-tab.component')` inside the host's `ngOnInit`, so the compiler places them in a **separate chunk** that is fetched on tab selection — NOT in the `/metrics` route fetch set (B4b).
- The host forwards the shell's `tabInputs()` for this tab onto the created ref via `setInput`, then drives `detectChanges()` (zoneless discipline, `app.ts:391` + the chat-root mount note).

**Why NOT `@defer` (considered, rejected):** `@defer` is template-driven and would also code-split, but its triggering semantics (viewport/immediate) and its placeholder/fallback slots add template surface that is harder to unit-test under jsdom, and it would be the **first** `@defer` site in the metrics page. The VCR pattern is the **only in-app precedent** (`app.ts:391`) and the dispatcher explicitly says "follow the app.ts:391 precedent for mechanics". We follow the precedent. (If the architect pass prefers `@defer`, the host contract — static `Type` in the map, heavy body in a separate chunk — is unchanged; only the mount mechanics differ.)

**Spec pin (lazy-mechanism):** `metrics.tool-usage.spec.ts` asserts (a) `METRICS_TAB_COMPONENTS['tool-usage'] === ToolUsageTabHostComponent` (not the heavy component), and (b) `tabs.components.ts` contains **no** static import of `tool-usage-tab.component` / `tool-usage.service` / `tool-usage.models`. Plus the bundle guard (Task 6) proves the heavy bytes are not in the route fetch set.

### Task 5 — Register the body

**File: `frontend/src/app/pages/metrics/tabs/tabs.components.ts`**
- Add `import { ToolUsageTabHostComponent } from './tool-usage-tab-host.component';`
- Add `'tool-usage': ToolUsageTabHostComponent,` to `METRICS_TAB_COMPONENTS`.

No other change. The `METRICS_TAB_BINDINGS` derivation (`.map`) stays untouched.

### Task 6 — The gate packs + the bundle re-measure

**New/extended specs:**

| Spec | File | What it proves |
|---|---|---|
| **tab pack** | `frontend/src/app/pages/metrics/metrics.tool-usage.spec.ts` (NEW) | the tab appears in the manifest with id `tool-usage` and label `Tool Usage`; the slot is `custom`; the request shapes (summary + daily); a 503 on the source ⇒ exactly ONE banner (single-banner discipline) and no Retry; the domain-only 404 ⇒ the domain-local copy; the empty state is DISTINCT from the error state; the empty state is HONEST (no fake data on 503); rows sorted by `calls_7d` desc; zero/near-zero rows visually distinct; per-agent breakdown expands on row click; stale response discarded (supersede token); tab-hidden polling stops; **template-safety pin**: NO `innerHTML` and NO `bypassSecurityTrust` in the new tab's template (AM-12d precedent); **lazy-mechanism pin**: the map value is the host, not the heavy component. |
| service | `frontend/src/app/services/tool-usage.service.spec.ts` (NEW) | URL shapes; `Accept: application/json`; the 15 s deadline mapping to the typed timeout error; the supersede token discards a stale answer; `params` forwards only set values (incl. `project_id` omitted ⇒ all-projects). |
| models | `frontend/src/app/models/tool-usage.models.spec.ts` (NEW) | the domain-local copy table is exhaustive over the domain-only outcomes (no silent fall-through). |
| manifest | `frontend/src/app/pages/metrics/tabs.manifest.spec.ts` (EXTEND) | 9 tabs; the ids array; the labels array; 2 custom / 8 central; the family-roster map unchanged (tool-usage is custom). |
| bundle guard | `frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts` (EXTENDED, not bypassed) | the existing guard's contract pins stay green (the artifact is read, not copied) AND the new tab's vendor bytes in ANY static fetch set == 0; the chart.js-vendor invariant stays GREEN. |

**The bundle discipline (frozen numbers, from `bundle-baseline.json`):**

| Guard | Frozen baseline | Threshold | This lane's exposure |
|---|---|---|---|
| B1 initial-total | 5 909 018 B | \|Δ\| ≤ 2 048 B | **ZERO** — the `/metrics` route is LAZY (`app.routes.ts`), so nothing new lands in the initial set |
| B2 chart.js marker in initial set | 0 | 0 hits | **ZERO** — no chart.js import in the new code |
| B3 static `from 'chart.js'` sites | 0 | 0 static sites | **ZERO** — the new body imports no chart library; charts ride the shared house renderer |
| B4a chart chunk | 206 857 B min / 71 054 B gz | ≤ 82 000 B gz | **ZERO** — no new dynamic chart import (house SVG is already in the route chunk) |
| **B4b the `/metrics` route fetch set, lazy-only** | **165 185 B** (frozen) / **184 866 B** (measured post nest-anomaly, 134 B headroom) | **≤ 188 000 B (RE-BASELINED by leader ruling R-08 — architect §6.2 / LEADER RULING; was ≤ 185 000 B)** | **THE BINDING ONE.** PREMISE CORRECTION (🔴 arithmetic): "lazy keeps B4b within the frozen bound" is FALSE — static residue ≈1.0–1.3 kB vs 134 B headroom; expected measured B4b under lazy ≈ 185,800–186,200 B. The lazy mechanism keeps the heavy body + service + models OUT of the route fetch set; only the thin host (~0.5 kB) + the manifest delta land in it. The re-baseline covers the architect-proven residue with ~2.2 kB margin; static fallback (≈205–210 kB) REJECTED. If the measured add breaches 188,000, the lane trims — it may NOT raise the threshold beyond the ruled value. |
| B4 vendor-static-edge invariant | 0 | 0 | **ZERO** (load-bearing catch) |
| component-style warning SET | 9 entries | SET DIFFERENCE (0 added, 0 removed) | the new body uses **inline styles** (the house pattern — `central-tab.component.ts` uses `styles: [\`…\`]`), so it adds **no** `.scss` warning entry |

**The re-measure step (mandatory, in this order)** — verbatim from `nest-anomaly-report/phase4-plan.md:265-268`:
```bash
cd frontend && npm ci                 # if node_modules is absent (the house prerequisite)
cd frontend && ./node_modules/.bin/ng build --configuration production
node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js <the dist dir>
```
The measured numbers are recorded **against the FROZEN baseline** (the artifact's numbers are the reference — `reproduces_at_tip_note` says a future re-measure must compare against the frozen values and must NOT overwrite them). `frontend/package.json` stays untouched.

**AOT-gap discipline note** (the FE conventions from the blueprint): NG8002 and NG0303/NG8001 are invisible to `tsc --noEmit` AND jest TestBed — only a full AOT compile catches them. **One `ng build --configuration development` pass after template-touching commits, before e2e.** Before trusting a plan-specified knob/env-var/selector, grep for a CONSUMER.

### Task 7 — The one pixel item

**One Playwright geometry item** (the house harness: geometry assertions, NOT screenshots — evidence goes to `.agents/shared/planning/tool-usage-metrics-p1/pixels/`).

**The capture: the auth-off tab state.**
- **Fixture:** the app with the tool-usage source answering `503 {error: {detail: {error: 'AUTH_NOT_CONFIGURED'}}}`, then navigate to `/metrics?tab=tool-usage`.
- **Geometry assertions:**
  1. the tab's door-state panel is **visible** and its bounding box is **non-degenerate** (width > 0, height > 0);
  2. the Retry button is **absent** (auth-off is not retryable);
  3. no table rows render (the empty/error state is honest — no fake data).

---

## 6. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | The B4b headroom (134 B post nest-anomaly) is exceeded by the manifest delta + thin host — PREMISE CORRECTION (🔴 arithmetic, architect §6.2): "lazy keeps B4b within the frozen bound" is FALSE; static residue ≈1.0–1.3 kB vs 134 B headroom; expected measured B4b ≈185,800–186,200 B. | High | Medium | The lazy mechanism keeps the heavy bytes out; the host is ~0.5 kB; the leader ruled R-08 YES — re-baseline 185,000 → 188,000 (covers residue with ~2.2 kB margin). Static fallback (≈205–210 kB) REJECTED; defer-phase-4 is the only fallback and is moot. Re-measure is mandatory; expected 185,800–186,200 B must be recorded against the re-baselined 188,000 bound. |
| R2 | The lazy host fails to mount the heavy body (VCR + zoneless CD) and the tab renders blank | High | Low | Mirror `app.ts:391` exactly (createComponent + setInput + detectChanges + catch on chunk failure with retry flag). The tab pack covers the mount. |
| R3 | The `days`/window mapping disagrees with the BE default (7) and the chart shows an empty series | Medium | Low | `defaultWindowS: '7d'` matches `days=7`; the service forwards `days` from the window; the models spec asserts the mapping. |
| R4 | The per-agent breakdown reads as "current severity" when it is a 7-day aggregate | Medium | Low | Copy discipline: the column header says "calls (7d)" and never "current". |
| R5 | The near-zero threshold (`NEAR_ZERO_CALLS_7D = 3`) is mistaken for a BE contract | Medium | Medium | Marked as a LOCAL UI constant in Task 2; the component documents it; not sent to the BE. |
| R6 | Auth-off copy says "the metrics door" for a domain door, which could mislead | Low | Low | Accepted — the nest-reports precedent uses the same shared component with the same justification (the panel lives inside the metrics page; the action is identical). |
| R7 | The AOT gap (NG8002/NG0303 invisible to tsc/jest) lets a template defect ship | Medium | Medium | One `ng build --configuration development` pass after template-touching commits, before e2e. |

---

## 7. Success criteria

- The `/metrics?tab=tool-usage` route renders the project selector, the tool-usage table (freq-sorted, zero/near-zero visually distinct), the per-agent breakdown on row expansion, and the trend chart (house-svg-trend) — with auth ON.
- With auth OFF, the tab renders exactly ONE fail-closed banner (no Retry, runbook pointer) and never blanks or storms requests.
- `tabs.manifest.spec.ts` carries 9 tabs, 2 custom / 8 central, and the ids/labels arrays include `tool-usage` / `Tool Usage`.
- `metrics.tool-usage.spec.ts`, `tool-usage.service.spec.ts`, `tool-usage.models.spec.ts` are GREEN.
- The bundle guard exits 0 with the re-measured B4b recorded against the **re-baselined 188 000 B** threshold (leader ruling R-08, architect §6.2; expected measured 185,800–186,200 B); `package.json` is untouched; the component-style warning SET is unchanged (9 entries).
  Amended by architect pass @ fd254b335 — LEADER RULING R-08 (§6.2)
- The lazy-mechanism spec proves the heavy body is not in the static import closure of `tabs.components.ts`.
- `ng build --configuration development` passes after template changes.
- No daemon file, no `bundle-baseline.json`, and no other tab's component is touched.

---

## 8. Verification gates (must be GREEN before the phase closes)

1. `cd frontend && npx jest --config jest.config.js src/app/pages/metrics/tabs.manifest.spec.ts` — green (9 tabs, 2 custom/8 central).
2. `cd frontend && npx jest --config jest.config.js src/app/pages/metrics/metrics.tool-usage.spec.ts src/app/services/tool-usage.service.spec.ts src/app/models/tool-usage.models.spec.ts` — green.
3. `cd frontend && npx jest --config jest.config.js src/app/pages/metrics/metrics.bundle-guard.spec.ts` — green (vendor-static-edge invariant holds).
4. `cd frontend && ./node_modules/.bin/ng build --configuration development` — green (AOT gap closure).
5. The bundle re-measure sequence (§Task 6) — guard exits 0; measured B4b recorded against the re-baselined 188 000 B bound (leader ruling R-08, architect §6.2; expected 185,800–186,200 B).
  Amended by architect pass @ fd254b335 — LEADER RULING R-08 (§6.2)
6. `cd frontend && npx playwright test --config playwright.metrics-gate.config.ts` (the one pixel item) — green.
7. `git diff --stat` (worktree) shows ONLY files under `frontend/src/app/pages/metrics/`, `frontend/src/app/services/tool-usage.service*`, `frontend/src/app/models/tool-usage.models*` — no daemon, no baseline, no other tab.

---

## 9. Assumptions (explicit)

- A1: The near-zero threshold (`NEAR_ZERO_CALLS_7D = 3`) is a UI affordance, not a BE contract; it is a local constant in the heavy component.
- A2: The per-agent breakdown needs no second fetch — `agents: [{ agent_id, calls_7d }]` is in the summary payload (per the frozen contract).
- A3: The trend chart shows ONE series for the SELECTED tool (not all tools overlaid) to keep the chart legible; the project selector + tool selector drive the daily fetch.
- A4: The 7-day window (`days=7`) is the measurement window the commission's ~7-day readout uses; `24h` is a secondary short-horizon view.
- A5: `ProjectService.listProjects()` is the source of the project selector's options (existing service; no new service needed).
- A7: The B4b re-baseline to 188,000 B is the leader ruling R-08 (architect §6.2 / LEADER RULING) — it covers the architect-proven ~1.0–1.3 kB static residue with ~2.2 kB margin; static fallback (≈205–210 kB) is REJECTED; defer-phase-4 is the only fallback and is moot (ruled YES). No other bundle-baseline.json edits are permitted.
  Amended by architect pass @ fd254b335 — LEADER RULING R-08 (§6.2)

---

## 10. Open questions for the orchestrator

- OQ1: If the architect pass changes the summary payload shape (e.g. drops `calls_7d` in favour of a different window), the ranking + sort + near-zero affordance must be re-derived — flag as a contract-break, not a silent adapt.
- OQ2 (RESOLVED by leader ruling R-08, architect §6.2 / LEADER RULING): The re-baseline is required for the residue (~1.0–1.3 kB static residue), NOT because lazy failed. Static-only fallback (≈205–210 kB) REJECTED. Defer-phase-4 remains the honest fallback only if the leader had refused any re-baseline (moot — ruled YES). Lazy remains PRIMARY. No silent threshold edit ever occurred — the re-baseline landed as a phase-4 task (bundle-baseline.json + spec pin + tester re-measure) per the ruling.
  Amended by architect pass @ fd254b335 — OQ2 resolved (§6.2 / LEADER RULING R-08)
- OQ3: The near-zero threshold (A1) and the "layer-2 candidate" marker copy are UI affordances; if the Emperor wants them data-driven (e.g. bottom-quartile by frequency), that is a Phase-2 concern, not Phase 1.

---

## Evidence citations (file:line)

- `tabs.manifest.ts:243-366` METRICS_TABS (8 entries); `:521` MetricsTabId; `:120-132` MetricsCentralTab (requires familyIds); `:133-160` MetricsCustomTab (no familyIds, ResourcesWindow).
- `tabs/tabs.components.ts:4-11` static imports; `:24-33` `Record<MetricsTabId, Type<unknown>>` map.
- `app.routes.ts:161` single lazy route for /metrics.
- `app.ts:391` dynamic-import precedent (chat root lazy-mount via VCR).
- `services/nest-reports.service.ts:34` domain-door precedent (prefix, auth posture); `daemon/routers/nest_reports.py:57` (prefix registration, `routers/__init__.py:36`).
- `services/metrics.service.ts:38-40` central door constants (no project param).
- `components/charts/house-svg-renderer/house-svg-renderer.component.ts:85` selector; `:68` variants; `:422/425/428/431/439/442` inputs; `:59/:61` viewBox 900×260.
- `bundle-baseline.json:43` threshold_B4b = 185000 → **re-baselined to 188000** by leader ruling R-08 (architect §6.2 / LEADER RULING); measured 184866 (134 B headroom) per `.agents/tester/RESULTS/2026-11-05-nest-anomaly-report-functional-gate.md:59/:125`; expected measured under lazy ≈ 185,800–186,200 B.
- `nest-anomaly-report/phase4-plan.md:86-115` (4-line integration precedent); `:265-268` (re-measure command sequence); `:306` (R-08 breach rule).
- `tabs.manifest.spec.ts:70-72` (8-tabs length), `:77-86` (ids array), `:90-96` (labels), `:96-100` (custom count), `:102-116` (family roster map), `:117-125` (custom-slot assertions).
- `nest-reports-tab.component.ts:28` import MetricsDoorStateComponent; `:106` imports array; `:159-164` door-state usage.
- `metrics.component.ts:120` tabs binding; `metrics.component.html:128-137` ngComponentOutlet render.
- `services/project.service.ts:9-13` ProjectService (project selector source).
