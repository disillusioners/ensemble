# Frontend Implementation Plan — `snapshot-uiux`

> **⚠️ SUPERSESSION (amendment pass, 2026-10-05):** Where this body conflicts with `sequencing.md` §1, **§1 is binding.**
>
> **Status:** Draft (PLAN-ONLY commission — no commits, no source edits)
> **Author:** planner[v2] via plan-creation worker
> **Date:** 2026-10-05
> **Workdir:** `/home/nea/ensemble-src-wt-snapshot-uiux`
> **Branch:** `feature/snapshot-uiux` @ `ac399874` (base = latest)
> **Companion artifacts:**
> - Design spec (canonical for AC + visual language): `.agents/shared/planning/snapshot-uiux/design/design-spec.md` (pinned SHA `2ca69147b51383e452ba9d4185cb43b573ee5575`)
> - Pixel-true mockup: `.agents/shared/planning/snapshot-uiux/design/mockups/snapshots-page.html` (1292 lines, hand-authored)
> - BE plan: NOT YET WRITTEN (planned in parallel; see §3 BE-Contract Assumptions for the surface this plan depends on)

---

## 1 · Objective

Ship a dedicated global `/snapshots` page that exposes the **read + admin surface** for the agent snapshot subsystem (R15 toggle + R16 metrics + per-snapshot observability) as a standalone Angular 21 standalone-component page, removing the two `<section>` blocks currently embedded in `/settings`. Outcome: an operator can browse, filter, and inspect every snapshot in the system without leaving `/snapshots`; the page reads as a peer of `/settings`, `/schedules`, `/skills`.

Testable one-liner: a single sentence — *"A user navigating to `/snapshots` from the gear menu sees a paginated, filterable list of every snapshot in the system; clicking a row opens a detail drawer with full metadata; the header toggle persists to the existing `PUT /api/settings/snapshot-create` endpoint; the `/settings` page no longer renders any snapshot blocks."* — when true, the feature is done.

---

## 2 · Scope

### 2.1 In scope

| # | Item | Source of truth |
|---|---|---|
| 1 | New global route `/snapshots` → lazy `SnapshotsComponent` | brief §1 [given]; verified at `frontend/src/app/app.routes.ts:50` sibling pattern (`/skills` etc. all use `loadComponent`) |
| 2 | New gear-menu item `{ label: 'Snapshots', icon: 'bookmarks', route: '/snapshots' }` placed directly after the Settings item, BEFORE the conditional `Database` / `Maintenance` appends (verified anchors: `app.ts:747` Database, `app.ts:792` Maintenance) — NOT "LAST", because those conditional items render AFTER the static array when their probes succeed | brief §1 [given]; design-spec.md §2.2 — also verified at `frontend/src/app/app.ts:555-559` existing array shape + `frontend/src/app/app.html:58` `@for (item of settingsMenuItems(); track item.route)` |
| 3 | `SnapshotsComponent` page host (filter bar + table + drawer) | design-spec.md §4.1 |
| 4 | `SnapshotDetailDrawerComponent` dedicated host (separate from page) | brief §1 [given]; design-spec.md §2.4 + §4.6 — mirrors `frontend/src/app/components/schedule-detail-drawer/` |
| 5 | `SnapshotService` (NEW, `providedIn: 'root'`) for the new list endpoint | design-spec.md §4.6 + §6.2; brief §3 [given — endpoint shape] |
| 6 | New model file `frontend/src/app/models/snapshot.model.ts` | implied by §7.1 of design-spec.md; no snapshot model exists today (verified — `frontend/src/app/models/` has no `snapshot*` files) |
| 7 | Relocation (DELETE) of two `<section>` blocks from `settings.component.html:242-321` and `settings.component.html:323-358` | brief §3 [given]; verified by reading `settings.component.html:242-321, 323-358` |
| 8 | Relocation (DELETE) of associated state + handlers from `settings.component.ts:107-142, 278-279, 715-810` | brief §3 [given]; verified by reading `settings.component.ts:107-142, 278-279, 715-810` |
| 9 | Spec updates — REMOVE the ONE snapshot `describe()` block (banner comment + mock classes + `describe('Agent Snapshots settings toggle (R15)')`) at `settings.component.spec.ts:1345-1533` (~190 lines total), AND the 3 snapshot mock lines at `:1566-1570` inside the Timezone suite's `TimezoneTestBedService` (they mock the snapshot methods being removed) | verified by reading `settings.component.spec.ts` on 2026-10-05 (amendment pass): snapshot banner starts `:1345`, describe spans `:1389-1532`, Timezone banner starts `:1534`. The `:1535-1996` Timezone suite is UNRELATED — no Timezone coverage may be deleted (amendment finding #1; the original `:1346-1985` / "~640 lines / two describe blocks" range was wrong and swallowed ~450 Timezone lines) |
| 10 | Spec additions — NEW `snapshots.component.spec.ts`, `snapshot.service.spec.ts`, `snapshot-detail-drawer.component.spec.ts`, plus a settings-clean regression spec | brief §7 [given]; repo pattern from `skill-usage-table.component.spec.ts` |

### 2.2 Out of scope

| # | Item | Reason |
|---|---|---|
| 1 | Per-agent `snapshot_enabled` gating toggle (a per-row toggle in the table) | Brief §9 [given] + design-spec.md §6.4 — gated on in-flight `feature/unify-spawn-tools` work (job `7e6a62db`); v1 must NOT surface this — **stub-free**, no hidden placeholders. |
| 2 | Per-column table-header sort (clickable `<th>`) | design-spec.md §6.5 — sort lives in the filter bar globally; per-column sort is a future enhancement. |
| 3 | URL query-param filter sync (shareable URLs) | Brief §5 [given — "EVALUATE … decide (cheap version acceptable)"] — **DECISION: skip in v1.** The design-spec.md AC-5.2 explicitly says URL keeps `?page=...` and `?limit=...` only as **browser history**, not deep-link state. v1 in-memory state only. |
| 4 | Mobile-specific responsive layout | design-spec.md AC-9.3 — desktop-first (min viewport 1280×800); horizontal scroll on smaller. |
| 5 | Digest rendering on the page (the digest is in the detail payload but rendered on-demand inside the drawer) | Brief §4(e) [given — "digest (lazy via ?include=digest; render strategy for large JSON — collapsed pretty-print + copy button; dedicated error state on digest fetch failure)"]. Decision below in §6.4. |
| 6 | New npm dependencies | design-spec.md AC-9.4 — only existing `@angular/material/*` modules. |
| 7 | Modifying `SettingsService.getSnapshotCreateEnabled / setSnapshotCreateEnabled / getSnapshotUsageMetrics` (URL constants stay — page re-uses them) | brief §3 [given — "Toggle stays GET/PUT /api/settings/snapshot-create (the UI relocates; the toggle API does NOT move)"] + design-spec.md §7.5 |
| 8 | Light-mode contrast review (project doesn't ship light mode today) | design-spec.md §3.1 + §6.7 — forward-compatible rule; defer actual audit until a light-mode rollout lands |

### 2.3 Adjacent features deliberately excluded

| Feature | Why not now |
|---|---|
| `/projects/:projectId/snapshots` (project-scoped route) | design-spec.md §2.1 — snapshots are system-level; project context is a filter, not a route segment |
| `snapshot_search` tool read path | design-spec.md §6.2 — that is the LLM-tool surface; this page is human observability; keep separate |
| `/api/settings/snapshot-usage-metrics` removal/relocation | Brief §3 [given — "Metrics ASSUMED at GET /api/snapshots/metrics (relocation recommended on BE side; if be-plan.md keeps the legacy path, only the service URL constant changes — note this fallback)"] — keep legacy path; document fallback in §3.6 |

---

## 3 · BE-Contract Assumptions (explicit)

> **None of these are committed.** The BE plan is being drafted in parallel. If the BE plan diverges, the FE plan adapts by changing the **URL constants only** (`SnapshotService.LIST_URL`, `SnapshotService.METRICS_URL`, `getById` URL builder, multi-value tag encoding choice) — the component/service shape does not change.

### 3.1 `GET /api/snapshots` — list endpoint (NEW; being designed in BE plan)

**Query params:**

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `project_id` | string | no | — | exact project id; "all projects" omits |
| `agent` | string | no | — | exact agent id; "all agents" omits. **Wire param is `agent`** (sequencing §1 D-2 — canonical, amendment #4). The TS interface field stays `agent_id`; `buildParams` maps it to `?agent=`. |
| `status` | string | no | — | **repeatable** — `?status=active&status=failed`. Empty selection = no param. 5 enum values: `active`, `superseded`, `running`, `failed`, `interrupted` |
| `tags` | string | no | — | **repeatable** — `?tags=domain:api&tags=runtime:py`. Tag mode is an **independent top-level param** (`tag_mode=all\|any`, default `all`). Encoded as repeated params (NOT comma-joined) — see §6.1 for the FE encoding decision. |
| `created_after` | ISO-8601 | no | — | derived from age preset (24h/7d/30d/all default → omit) or explicit date (optional — only if cheap on BE; spec accepts either) |
| `sort` | string | no | `created_at_desc` | enum — the **4 v1 keys** per sequencing §1 D-3: `created_at_desc` (default), `created_at_asc`, `title_asc`, `status_asc`. `warm_desc` **dropped** (not implementable in BE v1) → follow-ups. |
| `limit` | int | no | `25` | 10/25/50 from paginator — FE always sends an explicit value; the BE-side default is `50` (`ge=1, le=200`) per sequencing §1 D-6, which supersedes the design-spec's 25/50 page-size values (amendment #2a) |
| `offset` | int | no | `0` | from paginator |

**Response:**
```json
{
  "items": [
    {
      "id": "8a3f-c2e7-19d4-4f12-9b8e-a3c7d2e1f908",
      "project_id": "default",
      "created_by_agent_id": "coder",
      "target_instance_id": "5b4c47a4-…",
      "title": "version-pump-v0.13.9-after-cpo-rebuild",
      "domain_tags": ["domain:api", "runtime:py"],
      "status": "active",
      "supersedes_snapshot_id": null,
      "git_sha": "a3c7d2e1f908…",
      "git_branch": "feature/cpo-rebuild",
      "git_dirty": false,
      "repo_path": "/home/nea/ensemble-src",
      "runtime_version": "v0.17.0",
      "effective_model": "gpt-4o-2024-08-06",
      "created_at": "2026-10-05T18:54:12.000Z"
    }
  ],
  "total": 42
}
```

The list response envelope key is **`items`** (sequencing §1 D-1 — canonical; the `{snapshots, total}` draft above is superseded, amendment #4). It does **NOT** include `digest` or `task_summary` (full body) — keeps payloads small for a paginated table. Field list matches `design-spec-amendment-contract-reconciliation.md §4` (`SnapshotRow` shape, L262-281) exactly: the `task_summary` and `warm_spawn_count` fields are explicitly OMITTED from the list payload (sequencing §1 D-4 / D-5; amendment A-2 / D-5), and `project_name` is OMITTED (D-5 — no `projects` join in v1; FE renders the truncated `project_id` UUID in the Project column and `—` in the Warm column per §3.6 fallbacks).

### 3.2 `GET /api/snapshots/{id}?include=digest` — detail endpoint (NEW)

- Without `?include=digest`: returns the same `SnapshotRow` plus the additional drawer-only fields (`task_summary` full text, full timestamps set).
- With `?include=digest`: same plus the full `digest` payload (possibly large JSON blob).
- 404 → drawer error state with retry.

### 3.3 `GET /api/snapshots/metrics` — metrics endpoint (ASSUMPTION)

The brief draft says "ASSUMED at GET /api/snapshots/metrics (relocation recommended on BE side)". The FE plan assumes this is the new URL; if the BE plan keeps the legacy `GET /api/settings/snapshot-usage-metrics`, the **only** change is `SnapshotService.METRICS_URL = '/api/settings/snapshot-usage-metrics'` — see §6.6 fallback.

Response (unchanged from today):
```json
{
  "capture_counts": { "coder": { "created": 47 }, "tester": { "created": 23 } },
  "spawn_counts_per_snapshot": [{ "snapshot_id": "8a3f…", "count": 12 }]
}
```

### 3.4 `GET /api/settings/snapshot-create` + `PUT /api/settings/snapshot-create` — TOGGLE (UNCHANGED)

- The toggle endpoint does NOT move.
- The new page injects `SettingsService` (existing) for these two calls only.
- Request body: `{ enabled: boolean }`. Response: `{ enabled: boolean }`.

### 3.5 Project list — `GET /api/projects` (UNCHANGED)

Reuse `ProjectService.listProjects()` for the project filter dropdown. Verified at `frontend/src/app/services/project.service.ts:21-29` — already a signal-backed list. The new page injects `ProjectService` and reuses its `projects()` signal for the searchable-select options. (No new fetch — the project service is already populated at app boot by other pages.)

### 3.6 Fallback rules if BE diverges

| If BE plan… | FE change | Where |
|---|---|---|
| Keeps metrics at `/api/settings/snapshot-usage-metrics` | `SnapshotService.METRICS_URL = '/api/settings/snapshot-usage-metrics'` | `snapshot.service.ts` (1 line) |
| Drops `project_name` from list response | Page falls back to `project_id` truncated UUID in the table + drawer | column renderers only (2 small branches in template) |
| Does not implement `warm_spawn_count` join | Page renders `—` in the Warm column and omits the drawer warm-spawn-count section | 1 template branch + 1 drawer section omission |
| Returns `digest` inline (not gated by `?include=digest`) | `SnapshotService.getById()` drops the query string; everything else identical | 1 line in service |
| Status enum drops one of the 5 values | Remove that chip from `statusOptions`; status chip CSS rule for that variant unused | constants + 1 SCSS rule |

---

## 4 · File-by-file change list

### 4.1 NEW files (13 total — pass 6 amendment #2: +5 Tester-owned e2e rows)

| Path | Purpose |
|---|---|
| `frontend/src/app/models/snapshot.model.ts` | Type definitions: `SnapshotStatus` (5-value union), `SnapshotRow`, `SnapshotListResponse`, `SnapshotDetailResponse`, `SnapshotFilters`. Re-exported from `frontend/src/app/models/index.ts` (added one line: `export * from './snapshot.model';`). |
| `frontend/src/app/services/snapshot.service.ts` | `SnapshotService` (`providedIn: 'root'`). Owns `list(filters)`, `getById(id, { includeDigest })`. The metrics call also lives here (or — simpler — the page injects both `SnapshotService` and `SettingsService` and uses the latter for the metrics call). **Decision: put metrics call in `SnapshotService`** — single dependency surface for the page; matches the brief's "Metrics ASSUMED at GET /api/snapshots/metrics" framing. |
| `frontend/src/app/pages/snapshots/snapshots.component.ts` | Page host. Standalone. **Owns the list fetch + list state + seenAgents + paginator + drawer open/close** (pass 4 amendment #8: was delegating list to a self-fetching table; now calls `service.list()` itself). Delegates table rendering to a presentational `<app-snapshots-table>` (same package, same folder) that receives rows via input. See §5.2 for the full signal list. |
| `frontend/src/app/pages/snapshots/snapshots.component.html` | Page template (header + metrics strip + filter bar + drawer-container with table + drawer). |
| `frontend/src/app/pages/snapshots/snapshots.component.scss` | Page styles. Status chip `.status-*` rules (5 variants). |
| `frontend/src/app/pages/snapshots/snapshots-table.component.ts` | **Presentational** table sub-component (pass 4 amendment #8 — was self-fetching; now receives `rows / total / loading / error / hasActiveFilters / pageIndex / pageSize` as inputs; emits `rowClick` + `pageChange` + `retry` outputs ONLY; **no service injection, no fetch effect**). |
| `frontend/src/app/pages/snapshots/snapshots-table.component.html` | Table template (`<mat-table>` + paginator + skeletons + states). |
| `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.{ts,html,scss}` | **Stateful** drawer host (pass 4 amendment #7 — was pure presentational; now owns its own detail fetch + digest + 200KB guard + retry lifecycle). 3 files. Standalone. Receives `(snapshotId, isDrawerMode)` inputs; emits `(close)`, `(navigateToPredecessor)`. Folder placement matches `frontend/src/app/components/schedule-detail-drawer/` but the input contract is different (this drawer fetches; the schedule drawer is host-fed). |
| `frontend/playwright.snapshots.config.ts` | Dedicated Playwright config for the snapshots e2e (strict ports 18279/14199, `reuseExistingServer: false`, globalTeardown backstop; loaded via `--config` — base config untouched). Full mechanic: sequencing.md §4.3.1. **Owner: Tester, P6 first step** (commit `c8`). |
| `frontend/scripts/boot-e2e-snapshots-daemon.sh` | Disposable-PG + daemon bootstrap mirroring `boot-e2e-maintenance-daemon.sh` (PG :15532, daemon :18279, canary `GET /readyz` → `status == "ready"`). **Commit mode 100755 (`chmod +x` after authoring)** — the mirror `boot-e2e-maintenance-daemon.sh` is `-rwxr-xr-x`/100755 and Playwright's webServer execs it directly. **Owner: Tester, P6 first step** (commit `c8`). |
| `frontend/proxy.conf.snapshots.json` | Angular proxy config: `/api` → `http://localhost:18279`, `/ws` → `ws://localhost:18279` (mirrors `proxy.conf.e2e.json` shape; read directly by `ng serve --proxy-config`). **Owner: Tester, P6 first step** (commit `c8`). |
| `frontend/e2e/snapshots.spec.ts` | The automated e2e spec — 10 numbered steps + 11a-11c (sequencing.md §4.3); `data-test` selectors per fe-plan §5.6. **Owner: Tester, P6 first step** (commit `c8`). |
| `frontend/e2e/global-teardown-snapshots.ts` | Race-loser teardown backstop wired at `globalTeardown` in the snapshots config (mirrors `global-teardown-maintenance.ts`, maintenance config `:52`). **Owner: Tester, P6 first step** (commit `c8`). |

> **Component count: 2 components in `pages/snapshots/` (page + table), 1 component in `components/snapshot-detail-drawer/`, 1 service, 1 model — plus the Tester-owned e2e quintet (pass 6 amendment #2).** Total: **13 manifest rows = 15 files** (10 app files — 5 TS sources + 5 colocated templates/styles, the drawer row bundling its 3 — plus the 5 e2e files; manifest row count is the unit the header counts, pass 6 amendment #2). The app-file count is fewer than the design-spec §7.1 suggests because the metrics strip and filter bar are sections inside the page template (per design-spec §2.4 — only the drawer warrants its own folder).

### 4.2 EDITED files (5 total)

| Path | Edit |
|---|---|
| `frontend/src/app/app.routes.ts` | **ADD** one route BEFORE the `{ path: '**' }` wildcard at line 82: `{ path: 'snapshots', loadComponent: () => import('./pages/snapshots/snapshots.component').then(m => m.SnapshotsComponent), title: 'Snapshots' }`. Placement: immediately after `/schedules` (line 57) for visual grouping (Snapshots ↔ Schedules are both operator observability surfaces). Mirrors the lazy-load shape at `app.routes.ts:50` for `/settings`. |
| `frontend/src/app/app.ts` | **ADD** one item to `settingsMenuItems` at `app.ts:555-559`: `{ label: 'Snapshots', icon: 'bookmarks', route: '/snapshots' }`. Placement: **directly after Settings, BEFORE the conditional Database/Maintenance appends** (verified anchors: `app.ts:747`, `:792`) — the earlier "**LAST**" claim is wrong once those conditional appends render (amendment finding #7; preserves order Blueprints → MCP Servers → Settings → **Snapshots** → *conditional Database / Maintenance*). Conditional appends continue to use the same `update()` pattern; no change needed there. |
| `frontend/src/app/pages/settings/settings.component.html` | **REMOVE** two sections: `settings.component.html:242-321` (`<section class="setting-section editor-section">` for the toggle) and `settings.component.html:323-358` (`@if (snapshotMetrics()) { … }`). No other changes. |
| `frontend/src/app/pages/settings/settings.component.ts` | **REMOVE** the signal trio + computed + loaders: lines `12` (the `SnapshotUsageMetrics` type import), `107-142` (all snapshot-related signals and computeds), the `OnInit` calls at `278-279`, and the methods `loadSnapshotCreateEnabled`, `onSnapshotCreateSelectionChange`, `saveSnapshotCreateEnabled`, `loadSnapshotMetrics` at `715-810`. Keep the `settingsService` injection (still needed for other calls). |
| `frontend/src/app/pages/settings/settings.component.spec.ts` | **REMOVE** (amendment finding #1 — corrected range): (1) the snapshot block at `:1345-1533` — banner comment `:1345-1357`, `TestBedMockSettingsService` + `TestBedMockWorkspaceService` classes `:1359-1387`, and `describe('Agent Snapshots settings toggle (R15)')` `:1389-1532` (~190 lines, ONE describe block — not two); (2) the 3 snapshot mock properties at `:1566-1570` (`getSnapshotCreateEnabled`, `setSnapshotCreateEnabled`, `getSnapshotUsageMetrics` — the last is a 3-line statement) inside the Timezone suite's `TimezoneTestBedService` (`:1554-1580`). The `:1535-1996` Timezone suite itself is UNRELATED coverage — none of it is deleted. The earlier "`1346-1985` (~640 lines)" claim was wrong (it swallowed ~450 Timezone lines). |

### 4.3 Dangling-reference checklist (verified via grep)

| Symbol | Where referenced | Action |
|---|---|---|
| `snapshotCreateEnabled` | `settings.component.ts:107, 110, 112`; `settings.component.html:262, 268, 281, 287` | Removed wholesale (no other consumers — verified by `grep -rn snapshotCreate frontend/` → only these 2 files). |
| `savedSnapshotCreateEnabled` | `settings.component.ts:108, 112` | Removed. |
| `savingSnapshotCreate` | `settings.component.ts:109, 304, 317` | Removed. |
| `snapshotCreateDirty` | `settings.component.ts:110, 304, 317` | Removed. |
| `snapshotMetrics` | `settings.component.ts:118, 323, 334`; `settings.component.html:323, 334` | Removed. |
| `snapshotMetricsCaptureEntries` | `settings.component.ts:119, 334, 338`; `settings.component.html:334, 338` | Removed. |
| `snapshotMetricsSpawnEntries` | `settings.component.ts:131, 345, 348`; `settings.component.html:345, 348` | Removed. |
| `saveSnapshotCreateEnabled` | `settings.component.ts:761, 303` | Removed. |
| `onSnapshotCreateSelectionChange` | `settings.component.ts:750, 269, 288` | Removed. |
| `loadSnapshotCreateEnabled` / `loadSnapshotMetrics` | `settings.component.ts:723, 796, 278-279` | Removed. |
| `SnapshotUsageMetrics` type import | `settings.component.ts:12` | Removed. |
| `getSnapshotCreateEnabled / setSnapshotCreateEnabled / getSnapshotUsageMetrics` on `SettingsService` | `settings.component.ts:724, 764, 797` + `settings.component.spec.ts:1371-1375, 1566-1570` (amendment: 1566-1568 → 1566-1570; the `getSnapshotUsageMetrics` mock is a 3-line statement) | **STAY on the service** (new page uses them). Spec mocks that referenced them come out with the deleted describe block (the 1371-1375 block is INSIDE the deleted `:1345-1533` snapshot block; the 1566-1570 block is inside the Timezone suite's `TimezoneTestBedService` and is deleted in-place per amendment #1). |
| Spec references to `SettingsService.getSnapshotCreateEnabled / setSnapshotCreateEnabled / getSnapshotUsageMetrics` | `settings.component.spec.ts:1371-1375, 1451, 1463, 1476, 1501-1523, 1566-1570` | Removed with the deleted describe block + the in-place Timezone-suite mock removal (amendment #1). |

Verified by: `grep -rln "snapshotCreate\|snapshotMetrics\|SnapshotUsageMetrics\|loadSnapshotMetrics\|getSnapshotCreateEnabled\|setSnapshotCreateEnabled\|getSnapshotUsageMetrics" --include="*.ts" --include="*.html" frontend/src/` returned only the 4 files above. No dangling references outside.

### 4.4 NOT modified (explicit)

- `frontend/src/app/services/settings.service.ts` — the three snapshot methods stay verbatim. The new page injects `SettingsService` for them.
- `frontend/src/app/app.html` — the gear-menu template at `app.html:54-64` iterates `settingsMenuItems()` dynamically; no template change needed.
- Any other component / service / model file in the repo.

---

## 5 · Component / service structure (signal names + public methods — NOT full implementation)

### 5.1 `SnapshotService` (`frontend/src/app/services/snapshot.service.ts`)

```text
@Injectable({ providedIn: 'root' })
class SnapshotService {
  // URL constants (single source for the FE ↔ BE surface)
  static readonly LIST_URL    = '/api/snapshots';
  static readonly DETAIL_URL  = '/api/snapshots';      // + '/{id}' at call
  static readonly METRICS_URL = '/api/snapshots/metrics';  // FALLBACK: '/api/settings/snapshot-usage-metrics'

  // ── Signals (cached at the service layer; pages subscribe via observables) ──
  readonly items   = signal<SnapshotRow[]>([]);
  readonly total   = signal(0);
  readonly metrics = signal<SnapshotUsageMetrics | null>(null);   // type re-exported from settings.service (kept for back-compat) or moved here

  // ── HTTP methods (return Observables; callers subscribe) ──
  list(filters: SnapshotFilters): Observable<SnapshotListResponse>
  getById(id: string, opts: { includeDigest: boolean }): Observable<SnapshotDetailResponse>
  getMetrics(): Observable<SnapshotUsageMetrics>

  // ── Pure helpers (testable in isolation) ──
  buildParams(filters: SnapshotFilters): HttpParams   // encodes multi-value tags + status; tag_mode; maps filters.agent_id → `?agent=` (wire name per sequencing §1 D-2)
  computeAgeCutoff(preset: '24h' | '7d' | '30d' | 'all'): string | null   // ISO-8601 or null
}
```

`SnapshotFilters` shape (TypeScript):
```text
interface SnapshotFilters {
  project_id: string | null;
  agent_id:   string | null;                    // FIELD name stays agent_id; wire param is `agent` (D-2)
  status:     SnapshotStatus[];             // empty = all
  tags:       string[];                      // dim:value strings
  tag_mode:   'all' | 'any';                 // default 'all'
  age:        '24h' | '7d' | '30d' | 'all';  // default 'all' (D-7 — design-spec's 30d default superseded; 90d dropped)
  sort:       'created_at_desc' | 'created_at_asc' | 'title_asc' | 'status_asc';  // 4 v1 keys (D-3); warm_desc dropped → follow-ups
  limit:      number;                        // 10 | 25 | 50 (D-6)
  offset:     number;
}
```

### 5.2 `SnapshotsComponent` (`frontend/src/app/pages/snapshots/snapshots.component.ts`)

> **PASS 4 amendment #8 (LEADER RULING) — page host now OWNS the
> list fetch (NOT the table).** The page calls `service.list()` on
> filter/page changes and OWNS `records / total / loading / error`.
> The PAGE populates `seenAgents` in its fetch handler;
> `agentOptions` is a computed in the PAGE. The TABLE is reduced to
> a pure presentational component (§5.3) that receives rows via
> input and emits `rowClick` + page events only. This coheres with
> the host-owned pageIndex reset (filter change → same-signal-write
> reset → single `list()` call). The DRAWER is its own stateful
> component (§5.4) that owns its own lazy detail fetch + digest +
> 200KB guard + retry (LEADER RULING, #7).

```text
@Component({ selector: 'app-snapshots', standalone: true, ... })
class SnapshotsComponent implements OnInit {
  // ── Injected services ──
  private snapshotService = inject(SnapshotService);
  private settingsService = inject(SettingsService);    // for get/setSnapshotCreateEnabled
  private projectService  = inject(ProjectService);    // for project dropdown options
  private snackBar         = inject(MatSnackBar);
  private clipboard       = inject(Clipboard);

  // ── Header toggle state (mirror of settings.component.ts:107-113) ──
  readonly snapshotCreateEnabled         = signal(false);
  readonly savedSnapshotCreateEnabled    = signal(false);
  readonly savingSnapshotCreate          = signal(false);
  readonly snapshotCreateDirty           = computed(() => /* same as settings */);

  // ── Filter signals (passed down to <app-snapshots-table> as inputs) ──
  readonly filterProjectId  = signal<string | null>(null);
  readonly filterAgentId    = signal<string | null>(null);
  readonly filterStatus     = signal<SnapshotStatus[]>([]);
  readonly filterTags       = signal<string[]>([]);
  readonly filterTagMode    = signal<'all' | 'any'>('all');
  readonly filterAge        = signal<'24h' | '7d' | '30d' | 'all'>('all');
  readonly filterSort       = signal<SnapshotFilters['sort']>('created_at_desc');

  // ── Paginator state — HOST-OWNED (amendment #10) ──
  // The host owns pageIndex; the table receives it as an input and NEVER resets it.
  // RULE: any filter-signal write — including onClearFilters() — resets pageIndex to 0
  // in the SAME signal write, so exactly ONE request fires and it carries offset=0.
  readonly pageIndex        = signal(0);
  readonly pageSize         = signal(25);   // options [10, 25, 50] (D-6)

  // ── LIST-LEVEL state — HOST-OWNED (PASS 4 amendment #8) ──
  // The page calls `service.list(filters)` on filter/page changes and
  // owns these signals. The table renders `records()` directly via
  // its `rows` input; the table has NO service injection, NO own
  // records/total/loading/error signals, and NO constructor fetch
  // effect. The host's fetch handler also populates `seenAgents`.
  readonly records         = signal<SnapshotRow[]>([]);
  readonly total           = signal(0);
  readonly listLoading     = signal(false);
  readonly listError       = signal<string | null>(null);
  private readonly debouncedTags = signal<string[]>([]);     // 250ms debounce for chip input — host-owned

  // ── Drawer state ──
  // The drawer (`SnapshotDetailDrawerComponent`) owns its own lazy
  // detail fetch + digest + 200KB guard + retry (§5.4, #7). The page
  // only owns the id-swap signal that hands the drawer its new
  // snapshot to load.
  readonly drawerOpen          = signal(false);
  readonly selectedSnapshotId  = signal<string | null>(null);
  // `drawerSnapshot` is NOT here — the drawer fetches and owns its
  // own snapshot. The page only knows the id.

  // ── Metrics strip (delegated to its own inline template section; no separate component) ──
  readonly metrics             = this.snapshotService.metrics;   // signal-backed
  readonly metricsLoading      = signal(false);
  readonly metricsError        = signal<string | null>(null);

  // ── Computed ──
  readonly hasActiveFilters    = computed(() => /* any non-default */);
  readonly activeFilterCount   = computed(() => /* count of non-defaults */);

  // ── Static option lists ──
  readonly statusOptions: SnapshotStatus[] = ['active','superseded','running','failed','interrupted'];
  readonly sortOptions: { value: SnapshotFilters['sort']; label: string }[] = [...];
  readonly ageOptions: { value: SnapshotFilters['age']; label: string }[] = [...];

  // ── Project options (searchable-select shape) ──
  readonly projectOptions = computed<SearchableSelectOption<string | null>[]>(() => [
    { value: null, label: 'All projects' },
    ...this.projectService.projects().map(p => ({ value: p.project_id, label: p.name }))
  ]);

  // ── Agent-filter options (amendment #5 — MECHANISM PINNED: option (a)) ──
  // Distinct agent list derived client-side FROM the list responses this page
  // has already received: agentOptions = distinct(created_by_agent_id) over a
  // session-accumulated set (every row from every list fetch so far, across
  // pages and filter results), ALWAYS plus the currently-selected agent so a
  // set filter never vanishes from the dropdown.
  // WHY (a) over (b) a dedicated distinct-agents BE facet: it keeps the BE
  // contract FROZEN — no new endpoint, no sequencing §1 churn (D-2 remains the
  // whole agent surface on the wire).
  // DOCUMENTED DEGRADATION: an agent that has not appeared in any loaded list
  // response is simply absent from the options until a fetch surfaces it. The
  // filter is a convenience narrowing, not an exhaustive directory — acceptable
  // for v1; if it proves insufficient, the (b) facet is the follow-up (and
  // would then need a D-8 row in sequencing §1).
  // (PASS 4 amendment #8: seenAgents + agentOptions now LIVE in the PAGE host,
  // not in the table — populated by the page's fetch handler on every list
  // response. The table is presentational; it never sees seenAgents.)
  readonly seenAgents = signal<ReadonlySet<string>>(new Set());  // updated on every list() response, by the page's fetch handler
  readonly agentOptions = computed<SearchableSelectOption<string | null>[]>(() => {
    const seen = new Set<string>(this.seenAgents());   // accumulated on every list response
    const selected = this.filterAgentId();
    if (selected) seen.add(selected);
    return [
      { value: null, label: 'All agents' },
      ...Array.from(seen).sort().map(a => ({ value: a, label: a }))
    ];
  });

  // ── Public methods ──
  ngOnInit(): void                                  // 3 parallel fetches: toggle, metrics, list (the list fetch is kicked by the host now, not the table)
  onSnapshotCreateSelectionChange(enabled: boolean): void
  saveSnapshotCreateEnabled(): void                 // PUT + snackbar; identical to settings.component.ts:761-786
  onClearFilters(): void                            // reset every filter signal to default AND reset pageIndex to 0 in the SAME signal write (#10)
  onToggleTagMode(): void                           // toggle 'all' ↔ 'any'
  onRowClick(snapshot: SnapshotRow): void           // set selectedSnapshotId, open drawer (the drawer fetches its own detail)
  onCloseDrawer(): void                             // set drawerOpen false, selectedSnapshotId null
  onNavigateToPredecessor(id: string): void         // drawer emits; swap selectedSnapshotId (drawer re-fetches on id change)
  onCopySnapshotId(): void                          // clipboard.writeText + snackbar
  onPageChange(event: PageEvent): void              // paginator event from the table; update pageIndex + trigger re-fetch
  onRetryList(): void                               // error retry: re-trigger the list fetch

  // ── private ──
  private composeFilters(): SnapshotFilters                       // combine all filter signals + paginator
  private fetchList(): void                                       // THE list fetch — called by effect / onPageChange / onRetryList
  private populateSeenAgents(items: SnapshotRow[]): void          // adds distinct created_by_agent_id to seenAgents (PASS 4 #8)
}
```

The toggle + loaders for the toggle and metrics are pure methods on `SnapshotsComponent` (header toggle pattern is exactly the existing settings code, lifted + re-targeted — keeps the diff narrow and matches the brief's "Relocation moves ONLY the toggle + metrics blocks OUT" instruction).

The list-fetch effect is host-owned. An effect on the host watches the (debounced tags, filters, pageIndex, pageSize) tuple and calls `fetchList()` exactly once per change. The host's `fetchList()` calls `service.list(filters)`, updates `records`/`total`, sets `listLoading` true→false, and — on success — calls `populateSeenAgents(items)` to add the distinct `created_by_agent_id` values to the session-accumulated set. On error: `listError` is set, `records`/`total` are NOT touched (table shows the previous data; the error renders inline). The R11 stale-response race is handled at the host layer via a request-id signal (a new `listRequestId` increments on every fetch; stale responses are dropped before the state writes).

### 5.3 `SnapshotsTableComponent` (`frontend/src/app/pages/snapshots/snapshots-table.component.ts`)

> **PASS 4 amendment #8 (LEADER RULING) — table is now PRESENTATIONAL.**
> It receives rows via input and emits `rowClick` + page events
> ONLY. **No service injection. No own `records / total / loading /
> error` signals. No constructor fetch effect.** The page host
> (§5.2) calls `service.list()` and feeds the resulting
> `rows`/`total`/`loading`/`error`/`pageIndex` inputs. The
> `pageIndex` reset on filter change is host-owned (filter change
> → same-signal-write reset → single `list()` call — #10). The
> page-event output fires on user pagination; the host translates
> the event into a `pageIndex` signal write + a re-fetch.

```text
@Component({ selector: 'app-snapshots-table', standalone: true, ... })
class SnapshotsTableComponent {
  // ── Inputs (the 7 filter signals + 2 paginator signals + 3 list-state inputs) ──
  // Filter signals are read-only inputs (the host owns them; the table renders).
  readonly filterProjectId = input<string | null>(null);
  readonly filterAgentId   = input<string | null>(null);
  readonly filterStatus    = input<SnapshotStatus[]>([]);
  readonly filterTags      = input<string[]>([]);
  readonly filterTagMode   = input<'all' | 'any'>('all');
  readonly filterAge       = input<'24h' | '7d' | '30d' | 'all'>('all');
  readonly filterSort      = input<SnapshotFilters['sort']>('created_at_desc');
  readonly pageSize        = input<number>(25);
  readonly pageIndex       = input<number>(0);   // HOST-OWNED: the host resets it to 0 on every filter write (incl. Clear-filters) in the same signal write (#10); the table itself NEVER resets it

  // List-state inputs (PASS 4 #8 — was self-fetching; now pure presentational)
  readonly rows            = input.required<SnapshotRow[]>();
  readonly total           = input.required<number>();
  readonly loading         = input<boolean>(false);
  readonly error           = input<string | null>(null);
  readonly hasActiveFilters = input<boolean>(false);   // for the empty-state branch (filtered-empty vs zero-total)

  // ── Outputs ──
  readonly rowClick        = output<SnapshotRow>();     // user clicked a row → host opens drawer
  readonly pageChange      = output<PageEvent>();       // user paginated → host updates pageIndex + re-fetches
  readonly retry          = output<void>();            // user clicked the inline retry button → host re-fetches

  // ── NO service injection (PASS 4 #8) ──
  // The table is a pure presentational sub-component. All HTTP
  // happens in the host (`SnapshotsComponent`) per #8.

  // ── Display columns (matches §1.5 of design-spec.md) ──
  protected readonly displayedColumns: readonly string[] = [
    'title', 'project', 'agent', 'status', 'tags', 'created', 'warm', 'actions'
  ];

  // ── Computed ──
  readonly isEmpty         = computed(() => this.rows().length === 0 && !this.loading() && !this.error());
  readonly isFilteredEmpty = computed(() => this.rows().length === 0 && this.total() > 0 && this.hasActiveFilters());

  // ── NO constructor fetch effect (PASS 4 #8) ──
  // The constructor is empty (or contains only template-helper setup).
  // The host owns the fetch lifecycle.

  // ── Template helpers ──
  statusClass(s: SnapshotStatus): string                        // 'status-active', etc.
  visibleTags(row: SnapshotRow): string[]                       // first 2
  overflowCount(row: SnapshotRow): number                       // > 2 ? tags.length - 2 : 0
  formatRelative(iso: string): string                           // '2h ago', '3d ago'
  formatAbsolute(iso: string): string                           // '2026-10-05 18:54 UTC'
  trackById(_: number, row: SnapshotRow): string                // trackBy for @for
}
```

### 5.4 `SnapshotDetailDrawerComponent` (`frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.ts`)

> **PASS 4 amendment #7 (LEADER RULING) — drawer OWNS its lazy
> detail fetch.** The DRAWER component OWNS the detail fetch: it
> injects `SnapshotService`, fetches on `snapshotId` change, and
> owns its loading / error / detail / digest / digestError /
> showDigest signals — **INCLUDING the digest fetch, the 200KB
> guard, and the retry**. The PAGE host (`SnapshotsComponent`)
> owns LIST-level loading/error ONLY (per #8); the page passes the
> id via the `snapshotId` input and the drawer takes it from there.
> This coheres with the existing spec cases (f)(g)(h) at §8.1 row 3
> which already assume the drawer owns the fetch lifecycle.

```text
@Component({ selector: 'app-snapshot-detail-drawer', standalone: true, ... })
class SnapshotDetailDrawerComponent {
  // ── Inputs (PASS 4 #7 — was snapshot; now snapshotId) ──
  // The host passes the id; the drawer fetches its own detail.
  // The optional `isDrawerMode` input is reserved for future
  // full-page reuse (drawer vs full-page; defaults to drawer).
  readonly snapshotId     = input.required<string>();           // the id to fetch (PASS 4 #7)
  readonly isDrawerMode   = input<boolean>(true);               // reserved for future full-page reuse

  // ── Outputs ──
  readonly close                  = output<void>();
  readonly navigateToPredecessor  = output<string>();

  // ── Injected (PASS 4 #7 — was just clipboard) ──
  private snapshotService = inject(SnapshotService);             // drawer OWNS the detail fetch + digest fetch
  private clipboard       = inject(Clipboard);

  // ── Detail state (drawer-owned) ──
  readonly detail       = signal<SnapshotDetailResponse | null>(null);
  readonly detailLoading = signal(false);
  readonly detailError = signal<string | null>(null);

  // ── Digest state (drawer-owned; lazy — only on Show-digest click) ──
  readonly digest       = signal<Record<string, unknown> | null>(null);
  readonly digestLoading = signal(false);
  readonly digestError  = signal<string | null>(null);
  readonly showDigest   = signal(false);                         // toggled by onToggleDigest
  private readonly DIGEST_GUARD_BYTES = 200 * 1024;               // 200 KB guard per brief §4(e)

  // ── Constructor effect: re-fetch on snapshotId change ──
  // When the host swaps `selectedSnapshotId()` (row click, or
  // navigateToPredecessor → re-emit id), the drawer's snapshotId
  // input changes; this effect re-fires `getById(id, { includeDigest: false })`
  // — without the digest (the digest is lazy, see onToggleDigest).
  constructor() {
    effect((onCleanup) => {
      const id = this.snapshotId();
      if (!id) {
        this.detail.set(null);
        this.detailError.set(null);
        this.digest.set(null);
        this.digestError.set(null);
        this.showDigest.set(false);
        return;
      }
      this.detailLoading.set(true);
      this.detailError.set(null);
      this.digest.set(null);
      this.digestError.set(null);
      this.showDigest.set(false);
      const sub = this.snapshotService.getById(id, { includeDigest: false })
        .subscribe({
          next: (resp) => { this.detail.set(resp); this.detailLoading.set(false); },
          error: (err)  => { this.detailError.set(this.toMessage(err)); this.detailLoading.set(false); },
        });
      onCleanup(() => sub.unsubscribe());
    });
  }

  // ── Computed helpers ──
  formattedCreatedAt(): string                                      // '2026-10-05 18:54:12 UTC'
  formattedGitSha(): string                                        // monospace full
  digestRenderStrategy(): 'collapsed' | 'expanded'                 // default collapsed
  digestJsonString(): string                                       // JSON.stringify(digest, null, 2)
  digestTooLarge(): boolean                                        // JSON.stringify(digest).length > DIGEST_GUARD_BYTES
  truncatedSnapshotId(): string                                    // '8a3f-c2e7-19d4-…' (first 4 - next 4 - first 2)
  truncatedTargetInstanceId(): string                              // same truncation rule

  // ── Template handlers ──
  onCopyId(): void                                                 // clipboard.writeText(snapshot().id) + snackbar
  onCopyUuidShort(): void                                          // clipboard.writeText(truncated) + snackbar
  onSupersedesClick(): void                                        // emit navigateToPredecessor with the id
  onClose(): void                                                  // emit close
  onToggleDigest(): void                                           // local toggle; on first show, calls getById(includeDigest: true) and owns the digest fetch + 200KB guard + error state
  onRetryDetail(): void                                            // inline retry for detail fetch error
  onRetryDigest(): void                                            // inline retry for digest fetch error
  onCopyDigest(): void                                             // clipboard.writeText(digestJsonString()) + snackbar — works even when too-large

  // ── private ──
  private toMessage(err: unknown): string                          // 'Failed to load snapshot details: <msg>' shape
}
```

The drawer mirrors `ScheduleDetailDrawerComponent`'s file
placement (`frontend/src/app/components/schedule-detail-drawer/`),
not its input contract — `ScheduleDetailDrawerComponent` is
pure-presentational (host fetches, drawer renders); this drawer
is **stateful** (own fetch, own retry, own digest lifecycle) per
LEADER RULING #7. The two coexist in `components/` because both
are non-routable, but their internal ownership models are different.

### 5.5 The page host does NOT use a section registry

**Decision (per brief "RECOMMEND a structure — one container component + section children, vs maintenance-style registry; justify briefly"):**

Use **one container + table child + drawer child** (NOT the maintenance-style `sections` registry). Justification: the page has only THREE logical units (header toggle, metrics strip, filter bar, table + drawer) — the header toggle + metrics strip + filter bar are all small, scroll-flow sections with no internal state and no reuse case. A registry buys nothing here (the maintenance page justified its registry because each section has its own service, complex internal state, and guard logic — none of which applies to the snapshot toggle or the metrics strip). Adding a registry would be premature abstraction (the maintenance page's registry is load-bearing only because §2.4 of its spec needed "adding section 2 = one line, no template change" — the snapshots page does not have a "section 2" coming).

The drawer is extracted into its own component (not into the page template) because it has internal state (copy-id handler, predecessor navigation, scroll position) — same justification design-spec.md §6.6 gave. The table is extracted into its own component because it has its own data-fetching + paginator lifecycle — mirrors the `SkillUsageTableComponent` precedent.

### 5.6 Test-hook contract — canonical `data-test` selectors (13)

> **Purpose (pass 5 FINAL, item #3).** This section is the **contract
> surface** between dev-FE (who binds the selectors in component
> templates) and the e2e spec (`frontend/e2e/snapshots.spec.ts`,
> sequencing §4.3 — each step's Playwright assertion references these
> 13 selectors by name). The list is **closed**: exactly 13 selectors,
> named here, bound by dev-FE in the file column, asserted by the
> tester in the sequencing §4.3 step column. Any selector not on this
> list is OUT OF CONTRACT — dev-FE may add `data-test` attributes for
> internal Jest specs but the e2e spec only consumes these 13. Any
> selector the e2e spec needs that is not here is a plan-amendment-
> grade change (add to this table FIRST, then bind).

| # | Selector | Host element / template file | Binding site |
|---|----------|-------------------------------|--------------|
| 1 | `gear-menu` | `frontend/src/app/app.html` (app shell) | The gear-menu icon button (parent of `menu-snapshots`); bound once at app-shell level. |
| 2 | `menu-snapshots` | `frontend/src/app/app.html` (app shell) | The "Snapshots" item inside the gear menu — `app.ts:747`/`:792` per amendment finding #7 (after Settings, before conditional Database/Maintenance appends). |
| 3 | `paginator` | `frontend/src/app/pages/snapshots/snapshots-table.component.html` | The `<mat-paginator>` element wrapper inside `SnapshotsTableComponent` (asserted visible on every step-2 page-load assertion). |
| 4 | `paginator-page-1` | `frontend/src/app/pages/snapshots/snapshots-table.component.html` | The page-1 button on the `<mat-paginator>`; asserted visible on every step-3 filter-change (asserts `pageIndex` resets to 0). |
| 5 | `filter-tag-input` | `frontend/src/app/pages/snapshots/snapshots.component.html` | The tag text-input control in the filter bar (the only filter asserted via direct `fill()` in step 4). |
| 6 | `snapshot-drawer` | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` | The `<mat-drawer>` root container; asserted visible on row-click (step 5 + 11a) and not-visible after Escape (11a) / backdrop-click (11b). |
| 7 | `drawer-section` | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` | Each of the 7 section cards (Task summary, Git anchor, Runtime / Model, Supersedes chain, Tags, Timestamps, Context); asserted via `[data-test="drawer-section"] h3` count == 7. |
| 8 | `digest-pre` | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` | The `<pre>` element rendering the pretty-printed digest JSON (rendered collapsed by default, expanded only after "Show digest" click per §6.4). |
| 9 | `metrics-capture-card` | `frontend/src/app/pages/snapshots/snapshots.component.html` | The "Capture counts" card in the metrics strip (asserted visible on step 6; asserted ≥1 row when `capture_counts` non-empty). |
| 10 | `drawer-backdrop` | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` | The backdrop element rendered behind the open drawer; click on it closes the drawer (step 11b). |
| 11 | `drawer-close` | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` | The close (×) button on the drawer header; click emits `close` output (asserted works "at any point in the sequence" per step 11c). |
| 12 | `drawer-error` | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` | The "Failed to load snapshot details" error block; rendered on detail fetch abort (step 11c via `page.route('**/api/snapshots/*', route => route.abort())`). |
| 13 | `drawer-retry` | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` | The Retry button inside the drawer-error block; click re-fires the detail fetch (step 11c final assertion — drawer recovers after route restored). |

**Cross-reference:** the 10 numbered steps + 11a-11c of the e2e spec
in sequencing §4.3 each reference these selectors by name. The spec
does NOT hardcode any other selector string; the dev-FE binding is
therefore unblocked once the test-hook contract lands in this
section.

---

## 6 · Decisions and conventions

### 6.1 Multi-value tag encoding

**Decision: repeated params** (`?tags=domain:api&tags=runtime:py`). Reasons:
- The brief explicitly says "DECIDE repeat-param vs comma-joined and MATCH the BE contract note". BE contract note in §3.1 specifies `?tags=...&tags=...` (repeatable). Repeat params.
- Angular `HttpParams` natively supports `.append('tags', v)` in a loop — no string-concat needed.
- Test contract: unit-test `SnapshotService.buildParams()` for both forms.

### 6.2 Component placement: pages/snapshots/ (not components/snapshots/)

**Decision: `pages/snapshots/`** (matches design-spec.md §7.1 + the project convention — every routable page lives under `pages/`). The drawer (item 4) lives under `components/snapshot-detail-drawer/` because it is a non-routable component (same folder as `schedule-detail-drawer`). **The drawer's internal ownership is STATEFUL** per pass 4 amendment #7 (LEADER RULING — it owns its own detail fetch + digest + 200KB guard + retry lifecycle; injected `SnapshotService`); it is NOT a pure presentational mirror of `ScheduleDetailDrawerComponent`. The folder placement matches but the input contract is different: the schedule drawer's host owns the fetch; the snapshot drawer's host owns only the id. The two coexist in `components/` because both are non-routable, but their internal ownership models are different.

### 6.3 Spec-file naming convention

| New file | Matches |
|---|---|
| `snapshots.component.spec.ts` | `skill-usage-table.component.spec.ts` (amendment #11: closest REAL sibling — `schedules.component.spec.ts` does not exist anywhere under `frontend/src/`; verified 2026-10-05 by `find`) |
| `snapshots-table.component.spec.ts` | `skill-usage-table.component.spec.ts` |
| `snapshot-detail-drawer.component.spec.ts` | `schedule-detail-drawer.component.spec.ts` |
| `snapshot.service.spec.ts` | `skill.service.spec.ts` |
| `snapshot.model.spec.ts` | pattern from `skill.model.spec.ts` (exists; just for the type) |
| `settings.component.spec.ts` — REMOVE the per-snapshot describe block | n/a (deletion) |

### 6.4 Digest rendering

**Decision: collapsed pretty-print + copy button + dedicated error state + 200KB guard — all OWNED by the DRAWER** (pass 4 amendment #7, LEADER RULING).

- The DRAWER (not the page) injects `SnapshotService` and owns the lazy digest fetch. The page NEVER pre-fetches the digest.
- The drawer fetches the initial detail with `getById(id, { includeDigest: false })` on `snapshotId` change (effect in the constructor).
- The digest section is **hidden** by default. The "Show digest" button toggles `showDigest`; on first show, the drawer calls `getById(id, { includeDigest: true })` and stores the result in the drawer-owned `digest` signal. On subsequent shows, no re-fetch (cache hit in the drawer).
- On success: pretty-print `<pre>{{ json }}</pre>` (rendered collapsed by default per design-spec.md §4.6).
- On failure: inline error block ("Failed to load digest — Retry") in the section; the drawer exposes `onRetryDigest()` that re-fires the fetch.
- Copy button: `clipboard.writeText(JSON.stringify(digest, null, 2))` + snackbar "Copied digest". **Copy works even when the 200KB guard triggers** (the guard is render-only; the raw JSON is still copyable).
- Large-digest safety: if `JSON.stringify(digest).length > 200 KB`, render `[digest too large to display — use Copy]` instead of `<pre>` (the `digestTooLarge()` computed returns true; the template branches).
- Rationale: design-spec.md §4.6 expects digest in the drawer sections but doesn't specify gating; brief §4(e) [given] explicitly says "lazy via ?include=digest; render strategy for large JSON — collapsed pretty-print + copy button; dedicated error state on digest fetch failure" — matches.

### 6.5 URL query-param filter sync for shareability

**Decision: SKIP in v1** (matches design-spec.md AC-5.2 — "URL keeps ?page=... and ?limit=... only as **browser history**, not deep-link state in v1"). The brief asks me to evaluate; this is the cheaper version that matches the design-spec. Follow-up: add as a separate feature once user feedback warrants it.

### 6.6 Metrics endpoint fallback

**Decision: use `/api/snapshots/metrics` as primary URL.** If BE plan keeps the legacy path, change `METRICS_URL` constant only — `SnapshotService.getMetrics()` is the single touchpoint. Documented in §3.6.

### 6.7 Project dropdown source

**Decision: reuse `ProjectService.projects()` signal.** Already populated at app boot by other pages. The page injects `ProjectService` and reads its signal directly — no separate fetch, no caching.

### 6.8 No per-agent `snapshot_enabled` gating in v1

**Decision: NO hidden/stubbed controls.** Per brief §9 [given] + design-spec.md §6.4. The page header has only the global toggle. Per-agent toggles land with the `feature/unify-spawn-tools` work. The page has NO `display: none` placeholders, NO `if (false) { … }` stubs.

---

## 7 · UX states

### 7.1 Page-level states (the table area)

| State | Trigger | Rendering |
|---|---|---|
| Initial loading | First `list()` GET in flight + `records()` is empty | 5 skeleton rows (mirrors `skill-usage-table`); header + metrics strip render in parallel |
| Loaded (has rows) | `total() > 0`, no error | 8-column `<mat-table>` with paginator |
| Loaded (zero rows total) | `total() === 0`, no error, no filter active | Educational empty: icon + "No snapshots yet" + paragraph explaining what a snapshot is + hint to enable via the header toggle + docs anchor link (no real href in v1) |
| Loaded (filtered to zero) | `total() > 0` BUT `records() === []` AND `hasActiveFilters() === true` | Filtered-empty: icon + "No snapshots match your filters" + inline "Clear filters" button |
| Error (table) | `list()` rejected | Inline error block in table area ONLY (header + metrics strip still visible). Retry button. |
| Subsequent page loading | `pageIndex` change after first load | NO skeleton — replace-path: keep previous rows visible + show a thin progress bar at the top of the table area (mat-progress-bar mode="indeterminate") |

### 7.2 Drawer states

| State | Trigger | Rendering |
|---|---|---|
| Closed | `drawerOpen() === false` | Drawer not rendered (or rendered but `[opened]="false"`) |
| Loading | `drawerOpen() === true` AND `drawerSnapshot()` is null AND fetch in flight | Drawer panel + skeleton rows (title, status chip, 7 section headers with grey bars) |
| Loaded | `drawerSnapshot()` populated | 7 sections in order (per design-spec.md §4.6; **D-5: warm-spawn-count section OMITTED in v1** — the drawer's "Warm-spawn count" section is removed; the TABLE's "Warm" column stays, rendering `—`) |
| Drawer fetch error | Fetch rejected | Error block inside the drawer body: "Failed to load snapshot details — Retry" + close button still works |

### 7.3 Header toggle states

| State | Trigger | Rendering |
|---|---|---|
| Clean | `snapshotCreateDirty() === false` | Apply button disabled; no "Unsaved changes" hint |
| Dirty | `snapshotCreateDirty() === true` | Apply button enabled; "Unsaved changes" hint visible |
| Saving | `savingSnapshotCreate() === true` | Apply button shows spinner + "Applying..." text; disabled |
| Save error | PUT rejected | Snackbar error; toggle stays dirty; saved state unchanged |

### 7.4 Metrics strip states

| State | Trigger | Rendering |
|---|---|---|
| Loading | First metrics fetch in flight | Two skeleton cards (3 grey bars each) |
| Loaded (no data) | `metrics().capture_counts` empty | "No captures yet." inside Capture card; Warmed card hidden (per design-spec.md §4.3) |
| Loaded (has data) | `metrics()` populated | Two cards with sorted rows + footer note |
| Error | Fetch rejected | Inline small retry button in the metrics area (NOT a page-level error — the page is usable without metrics) |

---

## 8 · Test plan

### 8.1 New specs (4 files + 1 deletion)

| File | Cases (minimum) |
|---|---|
| `frontend/src/app/pages/snapshots/snapshots.component.spec.ts` | (a) renders header toggle + metrics strip + filter bar + table skeleton on init; (b) 3 parallel fetches fire on `ngOnInit` (toggle, metrics, **list — now HOST-OWNED per pass 4 amendment #8**); (c) clicking Apply in dirty mode calls PUT `service.setSnapshotCreateEnabled` with the right `enabled`; (d) header-toggle save-error → snackbar error, toggle stays dirty, saved state unchanged (§7.3); (e) Clear Filters button visible when `hasActiveFilters()`, hidden when default; (f) any filter change (incl. Clear-filters) resets `pageIndex` to 0 in the SAME signal write (#10 — host-side assertion); (g) clicking a row sets `selectedSnapshotId()` and `drawerOpen()=true` — **the drawer fetches its own detail (#7); the page does NOT pre-fetch the detail**; (h) closing the drawer clears `selectedSnapshotId()` (the page no longer holds `drawerSnapshot()` — it lives in the drawer per #7); (i) metrics fetch error → inline retry in the metrics area, page still usable (§7.4); (j) metrics empty → "No captures yet." in the Capture card + Warmed card hidden (§7.4); (k) R10 cold start — `projects()` empty on init → `listProjects()` called exactly once; **(l) PASS 4 #8 — agent-filter population: after every list response, `agentOptions` reflects the distinct `created_by_agent_id` values seen (the page's fetch handler calls `populateSeenAgents(items)`); an agent value seen on page 1 stays available after paginating to page 2 with different rows; (m) PASS 4 #8 — selected-agent pin: an agent option stays pinned/valid in `agentOptions` when it is the currently selected value but is absent from later list responses, AND the wire param `?agent=<id>` is emitted correctly (asserts `service.list()` was called with the right `agent_id` in `buildParams` → wire `?agent=`).** |
| `frontend/src/app/pages/snapshots/snapshots-table.component.spec.ts` | (a) **PRESENTATIONAL component — no service injection, no fetch in constructor** (pass 4 amendment #8); receives `rows`/`total`/`loading`/`error`/`pageIndex`/`pageSize` as inputs and renders them; emits `rowClick`, `pageChange`, and `retry` outputs only; (b) re-emits `pageChange` on paginator interaction; (c) skeleton shown only when `rows().length === 0` AND `loading()`; (d) error state renders retry (emits the `retry` output, host re-fetches); (e) empty + filtered-empty differentiated by `hasActiveFilters()` input; (f) `trackById` returns `row.id`; (g) **R11 in-flight race** — when the host swaps `rows()` while a previous render is up, the new `rows` are reflected (the table does no fetching, so the race is purely about the host's `listRequestId` — the test verifies the table simply re-renders whatever input the host provides); (h) **#10 wire case** — when the host swaps `pageIndex` to 0 in the same signal-write as a filter change, the table re-renders the new `rows()` and emits no fetch of its own; (i) **template-helper parity** — `statusClass('active')` returns `'status-active'`; `formatRelative(iso)` returns `'2h ago'`-shaped string; `visibleTags(row)` returns the first 2 tags; `overflowCount(row)` returns `tags.length - 2` for rows with >2 tags (kept per §5.3). |
| `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.spec.ts` | (a) **after the detail fetch succeeds, renders 7 sections in order** (D-5: warm-spawn-count section OMITTED; the 7 sections are: Task summary, Git anchor, Runtime / Model, Supersedes chain, Tags, Timestamps, Context) — **the drawer fetches its own detail per #7; the test seeds the service to return a `SnapshotDetailResponse` and asserts the sections render after the fetch completes** (pass 4 amendment #7: the previous framing assumed the host passed a fully-loaded `snapshot` input; that input is now `snapshotId` and the drawer does the fetch); (b) clicking Copy ID button calls `Clipboard.writeText` with `detail().id`; (c) clicking Supersedes emits `navigateToPredecessor` with the id; (d) clicking Close emits `close`; (e) dirty badge visible when `detail().git_dirty === true`; (f) loading state — skeleton rows render while the detail fetch is in flight (no `detail()` yet); (g) drawer fetch error → "Failed to load snapshot details — Retry" error block renders, Retry re-fires and recovers; (h) lazy digest — NO digest request fires on drawer open; `getById(id, { includeDigest: true })` fires ONLY on the "Show digest" click; (i) digest-fetch error → dedicated inline error block with Retry (§6.4); (j) 200KB digest guard — oversized digest renders the too-large message instead of `<pre>` AND Copy still works (copies the raw JSON string). |
| `frontend/src/app/services/snapshot.service.spec.ts` | (a) `buildParams` encodes single + repeated `tags` and `status` correctly, and maps `filters.agent_id` to the `agent` wire param (D-2 — asserts the param NAME is `agent`, not `agent_id`); (b) `tag_mode=all\|any` toggling produces the right param (🟢 amendment: was `tag_mode=all\|99` — typo); (c) `computeAgeCutoff('24h')` returns ISO 24h ago, `computeAgeCutoff('all')` returns null; (d) `list()` calls `LIST_URL` with the encoded params; (e) `getById(id, {includeDigest: true})` appends `?include=digest`; `includeDigest: false` omits; (f) `getMetrics()` calls `METRICS_URL`. |
| `frontend/src/app/models/snapshot.model.spec.ts` | Optional — type-only file; covered by the consumer specs above. Skip if no runtime behavior to test. |

**Per-file case counts (PINNED for the record, pass 5 FINAL item #7b):**
- `snapshots.component.spec.ts` — 13 cases (a-m).
- `snapshots-table.component.spec.ts` — 9 cases (a-i).
- `snapshot-detail-drawer.component.spec.ts` — 10 cases (a-j).
- `snapshot.service.spec.ts` — 6 cases (a-f).
- (Optional `snapshot.model.spec.ts` — 0 cases; type-only.)

**Subtotal of new specs = 13 + 9 + 10 + 6 = 38 cases** (per the
four spec-file rows in the table above; the optional model-spec
contributes 0). Plus **3 regression pins** (settings-clean = 1,
route = 1, menu = 1 — see §8.2 + §8.3). Total = **38 + 3
regression pins = 41 cases** that `cd frontend && npm test`
exercises. Sequencing §4.2 mirrors this same rollup; this row
exists here as the binding arithmetic against the four spec-file
rows above.

### 8.2 Settings-clean regression spec (in `settings.component.spec.ts`)

After removing the snapshot blocks, add ONE test to ensure the regression stays clean:

- `it('does not render the Agent Snapshots or Snapshot Usage Metrics sections')` — queries the settings component's rendered DOM for `h2` content `'Agent Snapshots'` and `'Snapshot Usage Metrics'`; both must be absent.

### 8.3 Route + menu regression spec (in `app.component.spec.ts` if present, else inline e2e)

- Route present: `'snapshots'` resolves to `SnapshotsComponent`.
- Gear menu item: `settingsMenuItems()` includes an item with `route === '/snapshots'` and `label === 'Snapshots'`.

### 8.4 Test patterns to follow

- `TestBed` + `provideNoopAnimations` + mock service with `useValue` — mirrors `skill-usage-table.component.spec.ts:82-90`.
- Factories: `makeSnapshotRow(overrides)` and `makeMetrics(overrides)` mirroring `skill-usage-table.component.spec.ts:23-62`.
- One `TestBed` block per scenario group; mock service methods as `jest.fn().mockReturnValue(of(...))`.
- For HTTP-level tests, prefer service-level `jest.fn()` mocks over `HttpTestingController` (faster, matches project precedent).

### 8.5 BUILD / TEST DISCIPLINE (verbatim — non-negotiable)

> **TypeScript checks ONLY via `frontend/`'s local devDependency — `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`.**
> **NEVER bare `tsc`/`npx tsc` outside `frontend/` — forbidden pattern in this project** (prior incident: `npx` fetched a rogue registry package called `tsc` — community tombstone, forensically proved benign but banned).
> **Jest runs via `frontend`'s local runner only** (`cd frontend && npx jest` or `cd frontend && npm test`, both delegate to `frontend/node_modules/.bin/jest`).

This block must appear in the developer's PR description verbatim.

---

## 9 · Coupling notes

### 9.1 Cross-branch coupling — `feature/unify-spawn-tools` (in-flight, PRE-implementation, job `7e6a62db`)

**Issue.** That branch's plan expects to edit `settings.component.html:249-330` (4 doc-string lines surrounding the same Agent Snapshots section our relocation is deleting). Verified by reading `/home/nea/ensemble-src-wt-unify-spawn-tools/frontend/src/app/pages/settings/settings.component.html:249-330` — same block.

**Mitigation.** After our PR lands on `feature/snapshot-uiux`, the `feature/unify-spawn-tools` branch **MUST rebase onto ours** before any further edits. Their commit sequence:
1. Their future edits to settings.component.html:249-330 will fail to apply (the lines are gone).
2. They need to either: (a) rebase onto our branch (preferred — single linear history), or (b) drop their snapshot-section edits and re-derive against the new `/snapshots` page.

**Flag in plan.** This is a coordination requirement, not a code change. The plan should be referenced in the cross-branch sync manifest.

### 9.2 v1 must NOT surface per-agent `snapshot_enabled` gating

**Stub-free.** Brief §9 [given]: "v1 must NOT surface per-agent snapshot_enabled gating (that registry field lands with their work) — plan a stub-free v1 (no hidden/disable placeholders)."

**Mitigation.** No `display: none` placeholders, no `if (false) { ... }` stubs, no commented-out per-row toggle cells. The page header has only the global toggle. Per-agent gates land with `feature/unify-spawn-tools`.

### 9.3 BE ↔ FE coupling (contract drift mitigation)

**Issue.** BE plan is being designed in parallel. If it disagrees with §3 of this plan, the FE plan needs to adapt.

**Mitigation.** All FE ↔ BE surface is concentrated in `SnapshotService` URL constants + the `SnapshotFilters` interface. The component / table / drawer / template are contract-blind — they consume typed `SnapshotRow` and `SnapshotDetailResponse`. Drift is contained to one file (`snapshot.service.ts`) plus possibly the `statusOptions` constant if a status is renamed.

### 9.4 `SettingsService` keeps its three snapshot methods

The methods stay on `SettingsService` (the URL constants don't move — `GET /api/settings/snapshot-create`, `PUT /api/settings/snapshot-create`, `GET /api/settings/snapshot-usage-metrics`). The new `SnapshotsComponent` injects `SettingsService` and calls these methods directly. **No service is renamed or restructured** to match the page.

### 9.5 No new `providedIn: 'root'` services conflict

`SnapshotService` is the only new `providedIn: 'root'` service. `ProjectService`, `SettingsService`, `MatSnackBar`, `Clipboard` are all existing injections. No collisions.

---

## 10 · Task breakdown (with hour estimates)

Sized so BE + FE together fit **one overnight implementation sitting** (per brief §10 [given]). The breakdown below is the FE portion only — ~6-8 hours of focused work.

> **⚠️ ATOMICITY MANDATE (amendment finding #6a):** there must be **no intermediate state where the metrics/toggle UI exists nowhere**. The Settings deletion (§10.3) and the new-page functional addition (§10.4–§10.6) land in the **SAME commit** (the combined relocation commit `c6` in sequencing §6.1). **Execution order is build-first-delete-last:** run 10.1 → 10.2 → 10.4 → 10.5 → 10.6 → **10.3** → 10.7. Task numbers below are UNCHANGED; only §10.3's position in the execution order moves to LAST.

### 10.1 Scaffold + route + menu (45 min)

| # | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 1 | Create empty page + table + drawer + service + model files | all NEW files in §4.1 (app files only — the 5 e2e rows are Tester-owned, authored as its first P6 step / commit c8, pass 6 amendment #2) | All files exist, `tsc --noEmit` passes (compiles only — no logic yet) | 10m |
| 2 | Add `/snapshots` route to `app.routes.ts` (line 82 above wildcard) | `app.routes.ts` | Route registered, title='Snapshots' | 5m |
| 3 | Add `{ label: 'Snapshots', icon: 'bookmarks', route: '/snapshots' }` to `settingsMenuItems` (directly after Settings, BEFORE the conditional Database/Maintenance appends at `app.ts:747`/`:792` — amendment #7) | `app.ts:555-559` | Gear menu shows new item, navigating reaches the (empty) page | 10m |
| 4 | Verify with `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` | — | tsc green | 5m |
| 5 | Verify route loads via `npm start` → click gear → Snapshots → empty container | — | Browser shows empty page with no console errors | 15m |

### 10.2 Service + model (60 min)

| # | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 6 | Write `snapshot.model.ts` (all 6 interfaces) | `models/snapshot.model.ts` + 1-line export in `models/index.ts` | tsc green; interfaces match §3.1 | 15m |
| 7 | Write `SnapshotService.list()`, `getById()`, `getMetrics()` | `services/snapshot.service.ts` | Service unit-test scaffold passes | 25m |
| 8 | Implement `buildParams()` with multi-value encoding + `computeAgeCutoff()` | `services/snapshot.service.ts` | Service spec covers all multi-value encodings + tag_mode | 15m |
| 9 | Write `snapshot.service.spec.ts` | `services/snapshot.service.spec.ts` | All cases in §8.1 pass | 5m |

### 10.3 Relocation (45 min)

> **Executed LAST** within §10 (see the atomicity mandate in the §10 preamble) — same commit as the new-page-functional code from §10.4–§10.6. T13's range was corrected in the amendment pass (the original `:1346-1985` / "~640 lines / two describe blocks" was wrong — it swallowed ~450 lines of the unrelated Timezone suite).

| # | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 10 | Remove `<section>` block (toggle) from `settings.component.html:242-321` | `settings.component.html` | HTML has no `snapshotCreate*` references; tsc green | 10m |
| 11 | Remove `<section>` block (metrics) from `settings.component.html:323-358` | `settings.component.html` | HTML has no `snapshotMetrics*` references; tsc green | 10m |
| 12 | Remove signal trio + loaders from `settings.component.ts:107-142, 278-279, 715-810` + type import at `:12` | `settings.component.ts` | tsc green; no dangling references (verified by §4.3 grep) | 15m |
| 13 | Remove the snapshot block `:1345-1533` (banner + mock classes + the ONE `describe('Agent Snapshots settings toggle (R15)')`) AND the 3 snapshot mock lines `:1566-1570` inside the Timezone suite's `TimezoneTestBedService` (amendment #1 — do NOT delete the `:1535-1996` Timezone suite) | `settings.component.spec.ts` | Spec runs; the remaining settings suites (incl. ALL Timezone tests) still pass | 10m |

### 10.4 Filter bar + header toggle (90 min)

| # | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 14 | Implement header toggle (lifted from settings.component.ts:107-142, 723-786) — 4 signals + 2 methods | `pages/snapshots/snapshots.component.ts` | Toggle renders, dirty gate works, Apply persists | 30m |
| 15 | Implement filter bar — 6 controls + Clear Filters + active-filter count badge | `pages/snapshots/snapshots.component.html` + `.ts` | All 6 controls render, Clear Filters resets, active-filter badge live-updates | 45m |
| 16 | Wire `ProjectService.projects()` to project dropdown | `pages/snapshots/snapshots.component.ts` | Dropdown shows all projects + "All projects" sentinel | 15m |

### 10.5 Table + paginator + drawer (120 min)

| # | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 17 | Implement `SnapshotsTableComponent` — **PRESENTATIONAL** (pass 4 amendment #8 — was self-fetching): 8 columns + paginator + skeleton + states; receives `rows`/`total`/`loading`/`error`/`hasActiveFilters` inputs; emits `rowClick` + `pageChange` + `retry` outputs ONLY. No `SnapshotService` injection. No own fetch effect. | `pages/snapshots/snapshots-table.component.ts` + `.html` + `.scss` | Table renders whatever rows the host provides; paginator re-emits `pageChange`; skeleton appears only on first load | 50m |
| 18 | Implement `SnapshotsComponent` wrapper — `mat-drawer-container` + table + drawer wiring; **HOST-OWNED list fetch** (pass 4 amendment #8): the page calls `service.list()`, owns `records`/`total`/`listLoading`/`listError`/`seenAgents`, and feeds the table via inputs. Click row → set `selectedSnapshotId()` + open drawer (the drawer fetches its own detail per #7). Escape / backdrop → close drawer (clears `selectedSnapshotId()` only — `drawerSnapshot()` lives in the drawer now). | `pages/snapshots/snapshots.component.ts` + `.html` | Page wires table inputs from host state; row click → drawer opens; Escape → drawer closes; click backdrop → drawer closes | 40m |
| 19 | Implement `SnapshotDetailDrawerComponent` — **STATEFUL** (pass 4 amendment #7 — was pure presentational): receives `snapshotId` input; injects `SnapshotService`; constructor effect re-fetches detail on `snapshotId` change (NO digest). 7 sections (D-5: warm-spawn-count section OMITTED) + Copy ID + Supersedes link. Lazy digest on "Show digest" click (`onToggleDigest()` → `getById(id, { includeDigest: true })`). 200KB guard via `digestTooLarge()` computed. Inline error blocks for detail and digest with Retry handlers. | `components/snapshot-detail-drawer/*` | Drawer renders 7 sections after fetch; Copy ID writes to clipboard; Supersedes emits; digest lazy-loads; 200KB guard branches; retry handlers recover | 30m |

### 10.6 Metrics strip + polish (45 min)

| # | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 20 | Implement metrics strip section — 2 cards + footer note + sorted rows | `pages/snapshots/snapshots.component.html` + `.scss` | Cards render, sorted desc by count, warmed card hidden when empty | 25m |
| 21 | Add SCSS — status chip colors (5 variants) + skeleton + responsive guard (1280px min) | `pages/snapshots/snapshots.component.scss` + `snapshots-table.component.scss` + `snapshot-detail-drawer.component.scss` | All 5 status chips visible with WCAG-AA contrast | 20m |

### 10.7 Specs + tsc + jest green (90 min)

| # | Task | Files | Acceptance | Est |
|---|---|---|---|---|
| 22 | Write `snapshots.component.spec.ts` (cases §8.1 row 1) | spec file | All cases pass | 30m |
| 23 | Write `snapshots-table.component.spec.ts` (cases §8.1 row 2) | spec file | All cases pass | 25m |
| 24 | Write `snapshot-detail-drawer.component.spec.ts` (cases §8.1 row 3) | spec file | All cases pass | 20m |
| 25 | Add settings-clean regression test (§8.2) | `settings.component.spec.ts` | Test passes; settings page no longer renders snapshot blocks | 5m |
| 26 | Final tsc + jest green | — | `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` returns 0; `cd frontend && npm test` returns 0; no `--bail` failures | 10m |

**FE total: ~495 min ≈ 8.25 hours.** Within overnight-implementation budget. The BE plan must land in parallel (~3-4 hours for the new endpoint + filter validation; well within the same overnight window).

### 10.8 Risk-padded estimate

Add **+30% buffer** for: (a) BE contract drift forcing `SnapshotService` constant changes; (b) subtle drawer-state interactions (Escape key + backdrop + close button race); (c) status-chip color contrast audit. Realistic worst case: **~11 hours FE**.

---

## 11 · Risks and mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | BE contract drifts from §3 assumptions after this plan is reviewed | Med | Med | All drift localized to `snapshot.service.ts` URL constants + `SnapshotFilters` interface. Component / drawer / table are contract-blind. Documented in §3.6 fallback table. |
| R2 | `feature/unify-spawn-tools` (job `7e6a62db`) edits `settings.component.html:249-330` while our relocation lands → merge conflict | Med | High | Document the rebase requirement in our PR description; flag in cross-branch sync manifest; verify with `git log --oneline feature/unify-spawn-tools` before merge. |
| R3 | Status-chip contrast fails WCAG-AA in light mode | Low | Med | design-spec.md §6.7 already specifies `font-weight: 600` for `.status-active` and `.status-running` in light mode. Add the rule unconditionally (cheap, forward-compatible). |
| R4 | Tags input chip fires one refetch per keystroke | Low | High | Debounce 250ms via a `setTimeout`-based effect (§5.3). Verify with mocked `setTimeout` in spec. |
| R5 | Drawer's `mat-drawer` focus trap blocks Apply button (focus stuck in drawer) | Med | Med | Use `MatDrawer` (not `MatSidenav`); use `[opened]` binding; do not call `cdkTrapFocusAutoCapture` manually. Verified at `frontend/src/app/pages/schedules/schedules.component.html:157-171` (sibling pattern uses MatSidenav — adapt to MatDrawer if needed). |
| R6 | Digest fetch fails → user sees blank section with no error state | Med | Med | Brief §4(e) [given] — render a dedicated error block in the digest section with Retry. Cover in spec. |
| R7 | Tab order: header toggle → filter bar → table rows → drawer | Low | Low | design-spec.md §1.9 (AC a11y) defines the order; use native `<input type="radio">` + `tabindex="0"` on rows. Spec covers Enter/Space. |
| R8 | BE returns `digest` payload > 200 KB → `<pre>` blocks render | Low | Low | brief §6.4 — guard with a size check + `[digest too large]` message instead of `<pre>`. |
| R9 | `SettingsService.getSnapshotUsageMetrics` URL drift if BE moves endpoint | Low | Low | Documented in §3.6 — fallback is `METRICS_URL` constant change only. |
| R10 | Project list not yet populated when user opens `/snapshots` cold (no prior page visit) | Med | Med | Inject `ProjectService` and call `projectService.listProjects()` in `ngOnInit` if `projects().length === 0` — cheap idempotent fetch; spec covers. |
| R11 | SnapshotService.list() in-flight when filter changes → race condition (older response overwrites newer) | Med | Med | Use `switchMap` OR track a request-id signal and discard stale responses. Spec covers. |
| R12 | Digest snapshot showing generic Job ID — no way to navigate from snapshot row to the originating instance/agent | Low | Med | Brief + design-spec mark this as TEXT ONLY in v1 — no links. Follow-up. |

---

## 12 · Follow-ups (out of scope for v1)

| # | Follow-up | Why deferred |
|---|---|---|
| F1 | URL query-param filter sync (`?project_id=…&tag_mode=…`) | design-spec.md §1.5 AC-5.2 — explicitly excluded for v1. |
| F2 | Per-agent `snapshot_enabled` gating (per-row toggle in the table) | design-spec.md §6.4 — gated on `feature/unify-spawn-tools`. v1 is stub-free. |
| F3 | Per-column table-header sort (`<th>` click handlers + `MatSort`) | design-spec.md §6.5 — sort is a global control in v1; revisit if user feedback warrants. |
| F4 | Mobile-responsive layout (<1280px viewport) | design-spec.md AC-9.3 — desktop-only in v1; horizontal scroll acceptable. |
| F5 | Light-mode contrast audit | design-spec.md §3.1 — project doesn't ship light mode today. |
| F6 | Project-scoped route `/projects/:projectId/snapshots` | design-spec.md §2.1 — system-level surface in v1; project filter is a control, not a segment. |
| F7 | Deep-link to a snapshot: `/snapshots/:id` | design-spec.md §4.5 — id-based fetch supports it (in service already) but no route; add when use case appears. |
| F8 | Drawer-from-row navigates to predecessor's drawer (§4.6 supersedes chain) | design-spec.md §4.6 + §1.6 AC-6.7 — SPEC'd for v1 actually (wired via `navigateToPredecessor` output). NOT a follow-up. |
| F9 | Snapshot deletion / prune action (operator-only) | out of brief scope; no v1 requirement. |
| F10 | "Capture a new snapshot" button on the page (delegated to agent runtime) | out of brief scope; v1 is read + admin only. |

---

## 13 · Acceptance criteria for THIS plan

| # | Criterion | How to verify |
|---|---|---|
| AC-P1 | A developer can implement the feature end-to-end without re-deriving any decision | Reading §4 (file list), §5 (signals/methods), §6 (decisions), §10 (tasks) covers everything |
| AC-P2 | Every file referenced in §4 exists at the cited line range, OR is a NEW file with a defined path | Verified via `read_file` + `grep_files` during planning |
| AC-P3 | Every dangling reference identified in §4.3 is removed by the task list in §10 | Each row in §4.3 maps to a task in §10.3 or §10.7 |
| AC-P4 | BE contract is explicit (§3) with fallbacks (§3.6) | Developer is not blocked on the parallel BE plan |
| AC-P5 | The brief's BUILD/TEST DISCIPLINE block is reproduced verbatim in §8.5 | grep "BUILD / TEST DISCIPLINE" → appears in §8.5 |
| AC-P6 | The brief's coupling notes are reflected in §9 | grep "unify-spawn-tools" → appears in §9.1; grep "per-agent snapshot_enabled" → §9.2 |
| AC-P7 | Hour estimates total ≤ overnight | §4.7 ≤ 8.25 hours FE; +30% risk buffer → ≤11 hours |
| AC-P8 | PLAN-ONLY discipline: no source file is modified by THIS plan (only `.agents/shared/planning/snapshot-uiux/fe-plan.md` is written) | Verified — only `write_file` target is the plan file itself |
| AC-P9 | The plan cites file:line for every verified claim | Each §X.Y block carries the citation; "[given]" marks caller-provided facts |

---

## 14 · End matter

- **Plan file path:** `/home/nea/ensemble-src-wt-snapshot-uiux/.agents/shared/planning/snapshot-uiux/fe-plan.md` (this file).
- **Reviewed artifacts:**
  - Design spec: `.agents/shared/planning/snapshot-uiux/design/design-spec.md` (755 lines, pinned)
  - Mockup: `.agents/shared/planning/snapshot-uiux/design/mockups/snapshots-page.html` (1292 lines)
- **Companion plans (out of band):**
  - BE plan — being drafted in parallel; this plan assumes §3 surface
- **Implementation ready:** YES — a developer with the repo access can implement end-to-end from §4 + §5 + §10 in a single sitting.

---

**END OF PLAN**