# Phase 2 — Frontend: Gear-Menu + Maintenance Section

Date: 2026-09-27
Author: planner[v2] via phase-2 detail worker
Branch: `feature/maintenance-console` @ `666c089d`
Build target: contract frozen in `plan-overview.md` §API Contract (FROZEN)
Companion: `plan-overview.md` + `research-findings.md` (same directory)

> **Scope of this document.** Concrete, component-level FE work for Section 1
> (Checkpoint Cleanup). This is the **detail** layer: the overview gives the
> 7 high-level tasks, the research gives the file:line evidence; this document
> gives the developer everything they need to start coding without re-deriving
> the patterns. Build ONLY against the frozen contract. No endpoint additions,
> no field renames — any deviation is recorded in the "Contract feedback"
> section at the end so the architect can ratify before merge.

> **Read order for the implementer.** Start with this file → skim
> `plan-overview.md` §API Contract + §FE Structure + §Phase 2 task list + §Design
> Decisions Q5/Q6 + §Test Strategy (FE) → jump to `research-findings.md` §3 + §4
> for citations.

---

## 0. Quick-reference facts (verified against worktree at 666c089d)

| Concern | Fact | Source |
|---|---|---|
| Angular version | Angular 21 (standalone components, signals) | `frontend/package.json` |
| Test runner | Jest 30 (`npm test`), no TestBed for component specs | `frontend/jest.config.js`, `frontend/package.json` |
| Material package | `@angular/material` (no `ng-zorro`) | `frontend/package.json` |
| API base path | `/api/maintenance/checkpoint-cleanup/*` (relative; `proxy.conf.json` proxies `localhost:4199 → localhost:8079`) | `frontend/proxy.conf.json` |
| Style budget (per component) | anyComponentStyle 8kB warning / **24kB error** | `frontend/angular.json` `budgets` |
| Initial bundle | 1MB warning / **6MB error** | `frontend/angular.json` `budgets` |
| Existing gear-menu trigger | `<button mat-icon-button [matMenuTriggerFor]="settingsMenu">` + `<mat-menu #settingsMenu>` | `frontend/src/app/app.html:54-58` |
| Static settings items | 3 hard-coded entries (Blueprints, MCP Servers, Settings) | `frontend/src/app/app.ts:554-558` |
| Availability-probe pattern | `checkMigrationAvailability()` appends `{label,icon,route}` to the signal on a probe-success; `error: () => { /* stays hidden */ }` swallows transport failures | `frontend/src/app/app.ts:737-755` |
| `ngOnInit` call site | `app.ts:720-724` calls `loadHealth`, `checkMigrationAvailability`, `checkPlaneAvailability` in order | `frontend/src/app/app.ts:720-724` |
| Service shape model | `MigrationService` — `@Injectable({providedIn:'root'})`, `inject(HttpClient)`, `readonly API_BASE = '/api/migration'`, signals for state, `Observable` returns, `tap`/`catchError`/`of` pipeline | `frontend/src/app/services/migration.service.ts:28-145` |
| Models home | `frontend/src/app/models/index.ts` (690 lines) — flat barrel; existing precedent: `MigrationAvailability` at line 620, `MigrationProgress` at line 651 | `frontend/src/app/models/index.ts:612-681` |
| Lazy route convention | `loadComponent: () => import('./pages/...').then(m => m.X)` ABOVE the `**` wildcard | `frontend/src/app/app.routes.ts:4-37` |
| Reusable confirm dialog | `ConfirmDialogComponent` + `ConfirmDialogData` (`title?`, `message?`, `confirmLabel?`, `cancelLabel?`, `destructive?`); `afterClosed()` emits `true | false | undefined` | `frontend/src/app/components/confirm-dialog/confirm-dialog.component.ts` |
| Caller applies dark theme | `panelClass: 'dark-modal-panel'` is applied by the caller, not by the dialog itself | `frontend/src/app/components/confirm-dialog/confirm-dialog.component.ts` (JSDoc); in-tree callers: `source-list.component.ts:66,80`, `agent-selector.component.ts:384` |
| Logic-mirror spec style | No TestBed; hand-rolled mock classes + plain TS; examples `MockApiService`/`TestableInstanceService`/`MockJobsComponent` | `frontend/src/app/services/instance.service.spec.ts:14-35`; `frontend/src/app/pages/jobs/jobs.component.spec.ts:122-168` |
| Source-grep pin pattern | `expect(componentSrc).toMatch(/.../)` against `fs.readFileSync(componentPath, 'utf8')` — matches production source verbatim | `frontend/src/app/pages/jobs/jobs-page.bindings.pins.spec.ts:380-409` |
| Playwright config | `workers: 1`, `fullyParallel: false`, `baseURL: http://localhost:4199`, `webServer` array auto-boots BE (`bash dev.sh` on 8079) + FE (`ng serve --port 4199`), `reuseExistingServer: true` — **the maintenance e2e project OVERRIDES this: dedicated port + `reuseExistingServer: false` + daemon canary [R-11, v3 fix pass]** | `frontend/playwright.config.ts` |
| Idle-gate hint | Migration's dialog wiring: `ref.afterClosed().subscribe(result => resolve(result === true))` — the canonical pattern for "treat `false` AND `undefined` as cancel" | `frontend/src/app/components/migration/migration.component.ts:225-233` |

---

## Task Breakdown (aligns to plan-overview.md §Phase 2, splits where useful)

7 parent tasks (T1–T7), each with sub-tasks, file paths, acceptance criteria, dependencies, and suggested commit slicing. Each sub-task is independently completable, builds in a worktree, and passes its own `tsc --noEmit` + Jest subset.

### T1. Service Layer — `checkpoint-cleanup.service.ts`

**Owner**: any FE worker. **Depends on**: nothing (model types compile from the FROZEN contract; no BE needed for compile-time).
**Goal**: typed HTTP surface for the 5 endpoints, with state signals and an internal poll helper.

#### T1.1. Model types in `frontend/src/app/models/index.ts`

Append at the end of the existing Migration section (after line 681) — keeps the barrel single-source-of-truth convention. **AM-17 (idempotency_key DROPPED), AM-14 (state enum + 409-adoption), AM-12 (advisory + expected_duration_ms_hint), AM-11 (dual-flavor keys), AM-10 (skipped[]), AM-6 (interrupted state), AM-13/AM-1 (new error codes).**

| Symbol | Shape | Notes |
|---|---|---|
| `CheckpointCleanupRunKind` | `'auto' \| 'manual_dry_run' \| 'manual_execute'` | Mirrors server schema §3 + §4. `manual_dry_run` never surfaces in `last_run` (AM-9). |
| `CheckpointCleanupRunStatus` | `'running' \| 'succeeded' \| 'failed' \| 'interrupted'` | **AM-6** — `interrupted` is added; `overlap_refused` was deleted by architect ruling. |
| `MaintenanceAvailabilityState` | `'ready' \| 'backend_unsupported' \| 'subsystem_disabled' \| 'kill_switched'` | **AM-14** — replaces boolean `eligible` for FE branching. `eligible` is derived (`state === 'ready'`). |
| `MaintenanceAvailability` | `{ eligible: boolean; state: MaintenanceAvailabilityState; backend: 'postgres' \| 'sqlite'; reason: string \| null }` | **AM-14, AM-13**. `reason` is diagnostic only — FE NEVER branches on `reason`. |
| `CheckpointCleanupConfig` | `{ checkpoint_max_per_thread: number; checkpoint_max_per_thread_floor: number; cleanup_interval_hours: number; blob_prune_dry_run_env_default: '0' \| '1'; blob_prune_destructive_armed: boolean }` | Frozen §2 (v3 [R-6]: the whole config block is read from LIVE env at request time; `blob_prune_destructive_armed` = effective auto-cycle dual-arm state — informational render). |
| `CheckpointCleanupSkippedEntry` | `{ thread_id: string; checkpoint_ns: string; reason: 'ZERO_REFS_FAIL_SAFE' \| 'MAX_REFS_EXCEEDED' \| \`ERROR:${string}\` }` | **AM-10**. Reason is machine-code-stable: closed set `{ZERO_REFS_FAIL_SAFE, MAX_REFS_EXCEEDED}` ∪ open family `ERROR:<ExceptionName>` — extensible enum. |
| `CheckpointCleanupBlobsSummary` | `{ scanned_pairs: number; would_delete_count: number; would_free_bytes: number; would_delete: number; bytes: number; deleted?: number; bytes_freed?: number; destructive: boolean; skipped: CheckpointCleanupSkippedEntry[]; skipped_truncated?: boolean }` | **AM-11**. Dual-flavor keys — FE branches on `destructive:bool` to pick the active set: dry-flavor (`would_delete_count`/`would_free_bytes` + legacy `would_delete`/`bytes` for shape symmetry) vs destructive-flavor (`deleted`/`bytes_freed`). `skipped_truncated:true` is set when BE caps `skipped[]` at 1000. |
| `CheckpointCleanupSummary` | `{ checkpoint_rows: { scanned_pairs: number; deleted: number; excess_pairs: number }; writes: { deleted: number }; blobs: CheckpointCleanupBlobsSummary; duration_ms: number }` | Frozen §2 / §5 `last_run.summary`. |
| `CheckpointCleanupLastRun` | `{ run_id: string; kind: 'auto' \| 'manual_execute'; started_at: string; completed_at: string \| null; status: 'succeeded' \| 'failed'; summary: CheckpointCleanupSummary }` | **AM-9** — `last_run` only includes `kind ∈ {auto, manual_execute}`; `manual_dry_run` never surfaces here. `kind: 'manual_dry_run'` is also stripped from the union. |
| `CheckpointCleanupInFlight` | `{ run_id: string; kind: CheckpointCleanupRunKind; started_at: string; triggered_by: string }` | Frozen §2. |
| `CheckpointCleanupStatus` | `{ config: CheckpointCleanupConfig; last_run: CheckpointCleanupLastRun \| null; in_flight: CheckpointCleanupInFlight \| null }` | Frozen §2. |
| `CheckpointCleanupWouldDelete` | `{ checkpoint_rows: number; writes: number; blobs: number; bytes: number }` | Frozen §3. |
| `CheckpointCleanupScanned` | `{ thread_ns_pairs: number }` | Frozen §3. |
| `CheckpointCleanupDryRun` | `{ run_id: string; would_delete: CheckpointCleanupWouldDelete; would_delete_count: number; would_free_bytes: number; scanned: CheckpointCleanupScanned; skipped: CheckpointCleanupSkippedEntry[]; skipped_truncated?: boolean; duration_ms: number; fresh_until: string }` | **AM-10, AM-11**. Canonical names: `would_delete_count` + `would_free_bytes`. `skipped[]` capped at 1000 with `skipped_truncated:true` flag. |
| `CheckpointCleanupExecuteRequest` | `{ dry_run_run_id: string; expected_bytes: number; confirm: true }` | **AM-17 DROPPED** — NO `idempotency_key` field (removed). Replaces earlier `idempotency_key?: string`. 409-adoption contract replaces it. |
| `CheckpointCleanupExecute` | `{ run_id: string; status: 'running'; started_at: string; advisory: 'system_busy' \| null; expected_duration_ms_hint: number }` | **AM-12 (A-11 RATIFIED)** — 202 body gains `advisory` + `expected_duration_ms_hint` (= the referenced dry-run's `duration_ms`; **unit: ms** [R-5, v3 fix pass] — canonical unit definition lives once in plan-overview §4). |
| `CheckpointCleanupRun` | `{ run_id: string; kind: CheckpointCleanupRunKind; status: CheckpointCleanupRunStatus; started_at: string; completed_at: string \| null; summary: CheckpointCleanupSummary \| null; error: { code: string; message: string } \| null }` | Frozen §5. `interrupted` carries `error.code = "run_interrupted"`. |
| `MaintenanceErrorCode` | union: `'not_initialized' \| 'not_found' \| 'run_in_flight' \| 'confirm_required' \| 'dry_run_required' \| 'dry_run_stale' \| 'byte_count_mismatch' \| 'backend_unsupported' \| 'origin_not_trusted' \| 'maintenance_disabled' \| 'internal_error'` | **AM-13, AM-1, A-8** — **11 codes (was 10 at v3; was 8 in the v1 draft)**. `origin_not_trusted` (403) + `maintenance_disabled` (503) added at AM-13/AM-1; `internal_error` (500, router catch-all per A-8) added to the docs at v3.1 [CF-6 doc-repair, v3.1] — the literal is already shipped on the wire. Derived from the `MAINTENANCE_ERROR_CODES` as-const tuple (see below). |
| `MaintenanceErrorBody` | `{ error: MaintenanceErrorCode; message?: string; details?: { run_id?: string; started_at?: string; expected?: number; stored?: number; age_seconds?: number; max_age_seconds?: number; [k: string]: unknown } }` | **AM-17 + AM-14** — `details.run_id` is the canonical key FE adopts on 409 (`run_in_flight`). Mirror `plane.py:71-170`. |

**Error-code literal set — single source of truth [CF-6 doc-repair, v3.1; codifies SHIPPED behavior]:** the FE ships (in `frontend/src/app/models/index.ts`) an as-const tuple from which the union and the exhaustiveness pin derive. This is documentation catch-up of an already-shipped, already-reviewed artifact (the FE reviewer + tester verified it; the architect's contrary D4 note was a stale-worktree read) — prescribe it so the spec matches what ships:

```ts
/** [CF-6 doc-repair, v3.1] The 11 maintenance error-code literals (A-8 set).
 *  Single source of truth: the union AND the T6.3 `error-code-union-exhaustive`
 *  count pin derive from this tuple — adding a 12th code is a one-line append
 *  here plus a pin-count bump, nothing else. */
export const MAINTENANCE_ERROR_CODES = [
  'not_initialized',
  'not_found',
  'run_in_flight',
  'confirm_required',
  'dry_run_required',
  'dry_run_stale',
  'byte_count_mismatch',
  'backend_unsupported',
  'origin_not_trusted',
  'maintenance_disabled',
  'internal_error',            // 500 — router catch-all per A-8 [CF-6 doc-repair, v3.1]
] as const;

export type MaintenanceErrorCode = (typeof MAINTENANCE_ERROR_CODES)[number];

/** Narrow an unknown wire value to a known code (used by `toErrorBody`). */
export function isKnownErrorCode(v: unknown): v is MaintenanceErrorCode {
  return typeof v === 'string' && (MAINTENANCE_ERROR_CODES as readonly string[]).includes(v);
}
```

**Acceptance**: `tsc --noEmit -p tsconfig.app.json` passes with all new types exported. No `@Injectable` or service-level logic in this sub-task. **[CF-6 doc-repair, v3.1]** `MAINTENANCE_ERROR_CODES` has exactly **11** entries; `MaintenanceErrorCode` is derived from it (no hand-written duplicate union); `isKnownErrorCode('internal_error')` is `true`.

**Commit slice**: `feat(checkpoint-cleanup): add typed models for maintenance API contract v3`.

**AM summary (T1.1)**: AM-17 (idempotency_key removed), AM-14 (state enum added to availability; `details.run_id` shape), AM-12 (`advisory` + `expected_duration_ms_hint` on 202), AM-11 (dual-flavor keys + canonical `would_delete_count`/`would_free_bytes`), AM-10 (`skipped[]` + `skipped_truncated`), AM-9 (`manual_dry_run` stripped from `last_run.kind` union), AM-6 (`interrupted` added to status union), AM-13 + AM-1 (two new error codes).

#### T1.2. Service class — `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.service.ts`

Mirrors `MigrationService` shape (`frontend/src/app/services/migration.service.ts:28-145`). **AM-14 + AM-17 + AM-6 amendments**: poll helper stops on `interrupted`, no idempotency_key on the execute call, and a dedicated helper exposes the 409-adoption run_id.

```ts
@Injectable({ providedIn: 'root' })
export class CheckpointCleanupService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/maintenance/checkpoint-cleanup';
  /** Source-grep pin (T6.3): AM-14 / A-10 — must stay 2000 ms. */
  static readonly POLL_INTERVAL_MS = 2000 as const;

  // Public state signals (read by the component template directly)
  readonly availability = signal<MaintenanceAvailability | null>(null);
  readonly status = signal<CheckpointCleanupStatus | null>(null);
  readonly lastDryRun = signal<CheckpointCleanupDryRun | null>(null);
  readonly lastError = signal<MaintenanceErrorBody | null>(null);

  // Computed convenience signals
  readonly isRunInFlight = computed(() => this.status()?.in_flight !== null);
  readonly canDryRun = computed(() => !this.isRunInFlight());
  /** AM-14 — derived `eligible` from the state enum; UI hides menu on any non-`ready`. */
  readonly isReady = computed(() => this.availability()?.state === 'ready');

  // ── HTTP methods (typed) ───────────────────────────────────────────────
  fetchAvailability(): Observable<MaintenanceAvailability> { /* GET /availability */ }
  fetchStatus(): Observable<CheckpointCleanupStatus>       { /* GET /status */ }
  dryRun(): Observable<CheckpointCleanupDryRun>             { /* POST /dry-run, body {} */ }
  execute(req: CheckpointCleanupExecuteRequest): Observable<CheckpointCleanupExecute> { /* POST /execute */ }
  getRun(runId: string): Observable<CheckpointCleanupRun>   { /* GET /runs/{run_id} */ }

  // ── Polling helper ────────────────────────────────────────────────────
  /**
   * AM-6: terminal status includes `interrupted` (boot-sweep CAS). AM-14: pure
   * poll — emits the final value once on terminal then completes. No side-effects.
   */
  pollRun(runId: string, intervalMs: number = CheckpointCleanupService.POLL_INTERVAL_MS): Observable<CheckpointCleanupRun> {
    /* setInterval(getRun, intervalMs) — terminates on status ∈ {succeeded, failed, interrupted}; unsubscribes on teardown */
  }

  // ── 409-adoption helper (AM-14, AM-17) ─────────────────────────────────
  /**
   * Returns the `run_id` from a 409 `run_in_flight` error body, or `null` if the
   * error is not a `run_in_flight`. Used by the component to resume polling the
   * in-flight run instead of surfacing an error toast — covers double-click and
   * network-retry classes (AM-17 DROPPED the `idempotency_key`; the run_id IS
   * the de-facto idempotency handle once a run is in flight).
   */
  adoptRunIdFromError(body: MaintenanceErrorBody | null): string | null {
    return body?.error === 'run_in_flight' && typeof body.details?.run_id === 'string'
      ? body.details.run_id
      : null;
  }
}
```

Notes:
- Use the **exact** path constants from the FROZEN contract — `/api/maintenance/checkpoint-cleanup/*` (relative; `proxy.conf.json` forwards).
- All `Observable`s use `pipe(tap(...), catchError(err => { this.lastError.set(this.toErrorBody(err)); return throwError(() => err); }))` so the component can surface a structured error from the signal AND the subscriber sees the throw.
- Error mapping helper `toErrorBody(err: HttpErrorResponse): MaintenanceErrorBody` accepts both `err.error` (FE-side parsed body) and falls back to `{ error: 'unknown', message: err.message }` when the body is absent (e.g., network error or proxy 502). This keeps `MaintenanceErrorCode` exhaustive at the union level but does not crash on malformed bodies. **[CF-6 doc-repair, v3.1]** `toErrorBody` validates via `isKnownErrorCode(v)` (derived from `MAINTENANCE_ERROR_CODES`): the 11 known codes — now incl. `internal_error` — surface **verbatim** (the pre-A-8 coercion of unknown codes to `not_initialized` is REMOVED); unknown codes and absent bodies fall back to `{ error: 'unknown', message: err.message }`.
- `pollRun` MUST:
  - Stop on terminal status (`'succeeded' | 'failed' | 'interrupted'` — **AM-6** adds `interrupted`).
  - Be Observable-shaped (not just a Promise) so the component can `takeUntilDestroyed` and avoid memory leaks when the user navigates away mid-poll.
  - Emit the terminal value one last time before completing (so the caller can update its `lastRun` signal from the poll's last emission).
  - Catch 404 (`not_found` — code literal unified to `not_found` [C-1, v3 fix pass]) and surface it via the same `lastError` signal — the BE keeps run rows persistent so 404 only happens for an unknown run id, never mid-poll.
  - Use `CheckpointCleanupService.POLL_INTERVAL_MS` as the **default intervalMs** (the constant is the pin target — T6.3 grep-pin verifies `POLL_INTERVAL_MS = 2000` in the source).
- **`adoptRunIdFromError` (AM-14, AM-17)** — pure helper; no HTTP. Component calls this on a 409 from `execute()` to decide between "show error toast" and "resume polling".
- **AM-1 note (Origin):** the FE sends **no special headers**. Same-origin SPA + localhost-family dev origins are auto-trusted by the BE's `require_trusted_origin` guard. Do NOT attempt no-cors/`fetch-mode: 'no-cors'` tricks — that hides the response body and breaks `details.run_id` adoption.

**Acceptance**:
- `tsc --noEmit -p tsconfig.app.json` passes.
- The service file is < 220 LOC (signal + 5 methods + poll helper + adoption helper + error-mapping helper).
- A standalone Jest spec (added in T6) proves all five HTTP methods map to the right path + verb, that `pollRun` terminates on each terminal status (including `interrupted`), and that `adoptRunIdFromError` returns the `details.run_id` on a 409 `run_in_flight` body and `null` otherwise.

**Commit slice**: `feat(checkpoint-cleanup): add service layer mirroring migration.service.ts (contract v3)`.

---

### T2. Gear-Menu Availability Probe — append Maintenance entry conditionally

**Owner**: any FE worker. **Depends on**: T1 (uses `MaintenanceAvailability` type). **AM-14 (state-enum gating), AM-13 (kill_switched state)**: branches on `state === 'ready'` not the legacy `eligible` boolean.

**Goal**: gear-menu shows "Maintenance" only when BE reports `state === 'ready'`. Hides cleanly on every other state (`backend_unsupported`, `subsystem_disabled`, `kill_switched`) — including the kill-switch OFF case, which the BE returns as 200 `state: "kill_switched"` (no error toast, the menu simply stays hidden).

#### T2.1. Add `checkMaintenanceAvailability()` to `frontend/src/app/app.ts`

Mirror `checkMigrationAvailability()` (app.ts:737-755) — same shape, new path, branch on **state enum** (AM-14):

```ts
private checkMaintenanceAvailability(): void {
  this.http.get<MaintenanceAvailability>('/api/maintenance/checkpoint-cleanup/availability').subscribe({
    next: (data) => {
      // AM-14: branch on state enum, NOT the legacy eligible boolean.
      // Hides on backend_unsupported / subsystem_disabled / kill_switched alike
      // — clean hide, never an error toast.
      if (data.state === 'ready' && !this.settingsMenuItems().some(i => i.route === '/maintenance/checkpoint-cleanup')) {
        this.settingsMenuItems.update(items => [
          ...items,
          { label: 'Maintenance', icon: 'build', route: '/maintenance/checkpoint-cleanup' },
        ]);
      }
    },
    error: () => {
      // Maintenance endpoint unreachable (lifespan not run yet, daemon
      // still booting, or transport failure). Section stays hidden —
      // an explicit "Maintenance unavailable" UX is out of scope.
      // AM-13: kill-switch OFF returns 200 with state:"kill_switched"
      // (not an error) — that case is handled by the state check above.
    },
  });
}
```

Call site: append `this.checkMaintenanceAvailability();` inside the existing `ngOnInit()` (app.ts:720-724), AFTER `checkPlaneAvailability()`. Order does not matter — each probe is independent — but keeping the calls grouped preserves diff locality.

Import the new type at the top of `app.ts`: `import type { MaintenanceAvailability } from './models';` (or wherever `MigrationAvailability`/`PlaneConfig` are imported — they're declared locally at app.ts:37 as `interface PlaneConfig { ... }` for plane and at app.ts the migration availability is imported via the same path).

[OQ-§6.3 RESOLVED, AM-14] Per overview §6.3, the menu MUST hide when the maintenance surface is not eligible. The default path (above) does exactly this, via the state enum. **Do NOT** add a "Maintenance unavailable" disabled-with-tooltip item; the spec says hide, not disable. **Do NOT** branch on `data.reason` — that field is a diagnostic string, not a contract signal (architect ruling in `architecture-recommendation.md` Focus Area 4).

[OQ-§6.2 RESOLVED, AM-13] Per overview §6.2, the menu MUST hide when `MAINTENANCE_ENDPOINTS_ENABLED=0` is set on the BE. The BE side owns the kill-switch and reports `state: "kill_switched"` from `/availability` — no FE-side env check needed, no FE-side `localStorage` flag. **AM-13 default**: `MAINTENANCE_ENDPOINTS_ENABLED=1`; OFF flips availability to `kill_switched`, gear menu hides cleanly. Phase 3 activation call-out: operators flipping the kill-switch need only a daemon restart.

**Acceptance**:
- `tsc --noEmit -p tsconfig.app.json` passes.
- Manual check: open `http://localhost:4199` against a PG-eligible dev daemon → gear menu shows "Maintenance". Switch to a SQLite-only daemon → `/availability` returns `state: "backend_unsupported"` → gear menu does NOT show "Maintenance". Daemon unreachable → `error:` branch runs → gear menu does NOT show "Maintenance". Restart daemon with `MAINTENANCE_ENDPOINTS_ENABLED=0` → `/availability` returns `state: "kill_switched"` → gear menu does NOT show "Maintenance".
- The probe is a no-op on retry: calling `checkMaintenanceAvailability()` twice does NOT append the item twice (the `!this.settingsMenuItems().some(...)` guard).
- The branch is on `state === 'ready'` — the legacy `eligible` boolean is tolerated by the type but NOT used by the probe (T6.3 source-grep pin).

**Commit slice**: `feat(checkpoint-cleanup): probe availability and append gear-menu entry (state-enum gating)`.

---

### T3. Route Registration — `frontend/src/app/app.routes.ts`

**Owner**: any FE worker. **Depends on**: T1 (page component class is the route target), T2 (state probe source). **AM-14 (route hardening)**: the lazy route is gated on `state === 'ready'` so a stale FE dist + missing BE router doesn't 404 into the SPA fallback.

**Goal**: lazy route for the new page is registered ABOVE the wildcard catch-all AND guarded with `canMatch` on availability state.

Insert ONE route entry immediately before `{ path: '**', redirectTo: '' }` (which is at the last position in `app.routes.ts`):

```ts
{
  path: 'maintenance/checkpoint-cleanup',
  loadComponent: () => import('./pages/maintenance/maintenance.component').then(m => m.MaintenanceComponent),
  // AM-14: route gating on availability.state === 'ready'.
  // canMatch (not canActivate) so the route is invisible to the router when not
  // ready — the router then falls through to the wildcard 404, which the SPA
  // renders, NOT the daemon. Prevents the stale-FE-dist + missing-BE-router
  // class of 404-into-SPA-fallback.
  canMatch: [maintenanceAvailabilityGuard],
  title: 'Maintenance · Checkpoint Cleanup',
},
```

Define `maintenanceAvailabilityGuard` as a `CanMatchFn` in `frontend/src/app/app.routes.ts` (or a sibling guards file):

```ts
import { inject } from '@angular/core';
import { CanMatchFn, Router } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { catchError, map, of } from 'rxjs';
import type { MaintenanceAvailability } from './models';

/**
 * AM-14 — canMatch guard for the maintenance section.
 * Returns true iff availability.state === 'ready'. On transport failure,
 * returns false (route is hidden, not errored) — matches gear-menu probe
 * semantics: clean hide, never an error toast.
 */
export const maintenanceAvailabilityGuard: CanMatchFn = () => {
  const http = inject(HttpClient);
  const router = inject(Router);
  return http.get<MaintenanceAvailability>('/api/maintenance/checkpoint-cleanup/availability').pipe(
    map((data) => (data.state === 'ready' ? true : router.parseUrl('/'))),
    catchError(() => of(router.parseUrl('/'))), // transport failure → fall through
  );
};
```

Notes:
- The route target is the **page shell** `MaintenanceComponent` (T4), NOT the section component directly. *(Confirmed as the single canonical route target — section-component-via-page-shell — by the v3 fix pass [R-10]; plan-overview §FE Structure now matches this exactly.)* The page shell hosts the section registry. Direct deep-links to `/maintenance/checkpoint-cleanup` go to the page shell which renders the section. This preserves the extensibility invariant — adding a section 2 later doesn't require touching `app.routes.ts`.
- `title:` matches the convention used by other lazy routes (`projects/:projectId/workspace`, `projects/:projectId/blueprints`).
- **Why `canMatch` (not `canActivate`):** `canMatch` lets the router evaluate other candidate routes on the path before deciding. With `canActivate`, the route matches first and only then does the guard run — too late to fall through cleanly. `canMatch` lets an unavailable route look absent to the router (matches the gear-menu probe semantics).
- **The guard does its OWN `/availability` probe.** This duplicates the probe in `app.ts`, but the duplication is intentional: the gear-menu probe runs at app boot, the route guard runs at navigation time (which may be minutes later, e.g. deep-link from bookmark, or after a state flip). The probe is cheap (one SELECT per AM-1/Focus-Area-3; same indexed single-row read pattern). T6.3 source-grep pin: the guard body contains `state === 'ready'`.

**Acceptance**:
- `tsc --noEmit -p tsconfig.app.json` passes.
- Manual check: navigating to `/maintenance/checkpoint-cleanup` against a PG-eligible dev daemon renders the page shell (T4). Navigating to `/maintenance/checkpoint-cleanup` against a SQLite-only daemon (returns `state: "backend_unsupported"`) routes to `/` (the `parseUrl('/')` fallback) — gear menu is also empty, so the deep-link path stays consistent. Navigating to `/maintenance` (no sub-path) still 404s (acceptable — we don't yet have a section index page).
- The guard's `state === 'ready'` check is pinned by T6.3 source-grep.

**Commit slice**: combined into T2's commit (route registration is co-located with the gear-menu probe and shares the state-enum semantics).

---

### T4. Section Registry + Page Shell — `frontend/src/app/pages/maintenance/`

**Owner**: any FE worker. **Depends on**: T1 (model + service), T3 (route target).
**Goal**: page shell that hosts a local section registry and renders the Checkpoint Cleanup section via `<ng-container *ngComponentOutlet>`.

#### T4.1. Page shell — `frontend/src/app/pages/maintenance/maintenance.component.ts/html/scss`

`maintenance.component.ts`:

```ts
import { Component, Type } from '@angular/core';
import { CheckpointCleanupComponent } from './checkpoint-cleanup/checkpoint-cleanup.component';

interface MaintenanceSection {
  readonly id: string;
  readonly label: string;
  readonly component: Type<unknown>;
}

@Component({
  selector: 'app-maintenance',
  standalone: true,
  imports: [/* CommonModule, ngComponentOutlet support */],
  templateUrl: './maintenance.component.html',
  styleUrl: './maintenance.component.scss',
})
export class MaintenanceComponent {
  // Local section registry — no shared cross-page registry. Adding a
  // section 2 later is a one-line append here.
  readonly sections: readonly MaintenanceSection[] = [
    { id: 'checkpoint-cleanup', label: 'Checkpoint Cleanup', component: CheckpointCleanupComponent },
  ];
}
```

`maintenance.component.html`:

```html
<div class="maintenance-container">
  <header class="maintenance-header">
    <h1>Maintenance</h1>
    <p class="maintenance-subtitle">Operator controls for daemon maintenance</p>
  </header>

  @for (section of sections; track section.id) {
    <section class="maintenance-section">
      <h2>{{ section.label }}</h2>
      <ng-container *ngComponentOutlet="section.component"></ng-container>
    </section>
  }
</div>
```

`maintenance.component.scss` — scaffold after `frontend/src/app/pages/settings/settings.component.scss` (color vars + `:host { display: flex; flex: 1; ... }` + `.maintenance-container { max-width: 820px; margin: 0 auto; padding: 2rem 1.5rem; }`). **Strict budget**: < 4kB so T5's component scss can use the remaining 20kB without bumping the 24kB anyComponentStyle cap.

**Acceptance**:
- Page renders a single `<h2>Checkpoint Cleanup</h2>` and the CheckpointCleanupComponent inside its `<ng-container>`.
- Adding a section 2 later is genuinely trivial: append `{ id: 'db-vacuum', label: 'DB Vacuum', component: DbVacuumComponent }` and it Just Works — no template change. **[R-22, v3 fix pass]** lazy-load note: the flat registry-rendered page structure is fine while sections stay few — **revisit page structure (per-section lazy chunks / sub-navigation) if sections ever exceed 5** (aligned with the BE-driven discovery thresholds in plan-overview §Standardization Boundary).

#### T4.2. Page-shell spec — `frontend/src/app/pages/maintenance/maintenance.component.spec.ts`

Logic-mirror (no TestBed):
- Plain `MaintenanceShell` mirror class with the same `readonly sections` array shape (copy verbatim from the production source so the mirror doesn't drift).
- Tests:
  - "registry contains exactly one section (`checkpoint-cleanup`)" — count pin.
  - "each section has the required fields (id, label, component)" — shape pin.
  - source-grep pin: `MaintenanceComponent` declares `readonly sections: readonly MaintenanceSection[]` (the readonly tuple is the type contract for downstream code; mirror breaks if production drops it).
  - template-grep pin: `@for (section of sections; track section.id)` exists in the html source.

**Commit slice**: `feat(checkpoint-cleanup): add maintenance page shell with local section registry`.

---

### T5. CheckpointCleanupComponent — full UI

**Owner**: any FE worker. **Depends on**: T1 (service), T4 (page shell). **Amendments in this section**: AM-17 (idempotency_key REMOVED — no client UUID generation), AM-14 (409-adoption of `details.run_id`), AM-12 (display `expected_duration_ms_hint`), AM-11 (dual-flavor branch on `destructive`), AM-10/A-3 (skipped[] render + reason badge map + `ERROR:*` fallback + `skipped_truncated` notice), AM-6 (interrupted state render — "re-run to converge" affordance, no cancel button in v1), AM-1 (Origin note: FE sends no special headers; localhost-family + same-origin auto-trusted), AM-13 (state enum gating mirrors T2/T3).

**Goal**: status block (dual-flavor) + dry-run button + result panel (skipped[] + fresh_until + honest minutes-scale copy) + execute confirm flow + poll + result panel + structured error rendering for all 10 stable codes.

#### T5.1. Component class — `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.ts`

Public surface (mirrors `MigrationComponent` shape, expanded for v2 contract):

```ts
@Component({
  selector: 'app-checkpoint-cleanup',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule, MatProgressBarModule, MatProgressSpinnerModule, MatSnackBarModule],
  templateUrl: './checkpoint-cleanup.component.html',
  styleUrl: './checkpoint-cleanup.component.scss',
})
export class CheckpointCleanupComponent implements OnInit, OnDestroy {
  private readonly service = inject(CheckpointCleanupService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);

  // Re-expose service signals for the template
  readonly status = this.service.status;
  readonly lastDryRun = this.service.lastDryRun;
  readonly lastError = this.service.lastError;
  readonly canDryRun = this.service.canDryRun;
  readonly isRunInFlight = this.service.isRunInFlight;

  // Local UI state
  readonly dryRunning = signal(false);
  readonly executing = signal(false);
  readonly executeProgress = signal(0); // 0..100; for the progress bar
  readonly activeRunId = signal<string | null>(null);
  /** AM-12 — display hint from the 202 body. null until first 202 received. */
  readonly expectedDurationHintMs = signal<number | null>(null);

  // Polling subscription — track so OnDestroy can tear down
  private pollSub: Subscription | null = null;

  ngOnInit(): void {
    this.refreshStatus();
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }

  // ── Actions ────────────────────────────────────────────────────────────
  refreshStatus(): void { /* service.fetchStatus().pipe(takeUntilDestroyed).subscribe() */ }
  onDryRun(): void { /* service.dryRun().pipe(...).subscribe() — sets dryRunning signal */ }
  onExecute(): void {
    // 1. validate preconditions locally (must have a fresh dry-run)
    // 2. open ConfirmDialogComponent with destructive: true, panelClass: 'dark-modal-panel'
    // 3. on confirm true: build payload {dry_run_run_id, expected_bytes, confirm: true}
    //    — AM-17: NO idempotency_key field. The 409-adoption contract replaces it.
    // 4. on 202: store activeRunId, stash expectedDurationHintMs, start polling via service.pollRun()
    // 5. on 409 run_in_flight: AM-14 — adopt details.run_id, resume polling (no error toast)
    // 6. on terminal: clear executing signal, update status, snack-bar the outcome
  }

  // ── Display helpers (pure; protected for template) ─────────────────────
  /** AM-11 — branch on destructive; return human-readable label + value. */
  protected blobCountFor(summary: CheckpointCleanupBlobsSummary): { label: string; value: number } {
    return summary.destructive
      ? { label: 'Blobs deleted', value: summary.deleted ?? 0 }
      : { label: 'Would delete (blobs)', value: summary.would_delete_count };
  }
  protected blobBytesFor(summary: CheckpointCleanupBlobsSummary): { label: string; value: number } {
    return summary.destructive
      ? { label: 'Bytes freed', value: summary.bytes_freed ?? 0 }
      : { label: 'Would free', value: summary.would_free_bytes };
  }
  /** AM-10 — known reason → human label + tone; unknown → generic. */
  protected skippedReasonLabel(reason: string): { label: string; tone: 'safe' | 'limit' | 'error' } {
    if (reason === 'ZERO_REFS_FAIL_SAFE') return { label: 'Fail-safe (no refs)', tone: 'safe' };
    if (reason === 'MAX_REFS_EXCEEDED')    return { label: 'Ref cap exceeded', tone: 'limit' };
    if (reason.startsWith('ERROR:'))       return { label: `Error: ${reason.slice('ERROR:'.length)}`, tone: 'error' };
    return { label: reason, tone: 'error' };
  }
  /** AM-6 — interrupted runs render with a "re-run to converge" affordance. */
  protected canRerunInterrupted(run: CheckpointCleanupRun): boolean {
    return run.status === 'interrupted';
  }

  formatBytes(n: number): string { /* KB/MB/GB, binary */ }
  formatDuration(ms: number): string { /* "1.8s" / "412ms" / "2m 14s" for long ops */ }
  formatTimestamp(iso: string): string { /* locale string (matches migration's logTime pattern) */ }
}
```

Key wiring details (these are the non-obvious bits a future maintainer needs to see in code, not in prose):

- **AM-17 — NO idempotency key.** The execute payload is **exactly** `{ dry_run_run_id, expected_bytes, confirm: true }`. **No `crypto.randomUUID()` call exists in this component.** The earlier OQ-§6.8 client-side UUID is DROPPED. The `idempotency_key` field is removed from the typed model AND from any payload assembly in this file. Source-grep pin `no-idempotency-key` (T6.3) asserts `crypto.randomUUID` does NOT appear in the execute flow AND `idempotency_key` does NOT appear in the wire payload.

- **AM-14 — 409-adoption on `run_in_flight`.** When `service.execute()` throws with `lastError()?.error === 'run_in_flight'` and `details.run_id` is a string, the component calls `service.adoptRunIdFromError(lastError())` and, if non-null, **immediately starts polling** `GET /runs/{details.run_id}` via `service.pollRun()`. **No error toast is shown for this case** — it's the normal "another run is already in flight, let me ride along" UX. The component sets `executing(true)` and shows the polling progress UI just as it would for a fresh run. The 409 response with `details.run_id` is the de-facto idempotency handle (AM-17).

- **AM-12 — `expected_duration_ms_hint` rendering.** The 202 response body carries `expected_duration_ms_hint` — **the value is in milliseconds** [R-5, v3 fix pass; the old "ceil-seconds" wording was wrong]. The component stashes it in `expectedDurationHintMs` and the template renders friendly copy DERIVED from the ms value: *"Expected ~X minutes"* for large values (via `formatDuration(ms)`), *"~Xs"* for small ones. Honest copy: the BE's hint is based on the dry-run's per-pair scan time; the actual execute may be faster or slower depending on I/O.

- **AM-6 — Interrupted state render.** When a polled run returns `status: 'interrupted'` (boot-sweep CAS'd a stale `running` row), the result panel renders an inline warning card with the message **"Daemon restarted mid-run — re-run to converge"** and a primary-action button that re-runs the dry-run + execute flow. **No cancel button in v1** (no abort point in the prune loop today — see overview §Out-of-Scope #3; v2 insertion point = per-pair loop top). Prune is retention-idempotent (re-run converges; per-pair independence means a partial pass finishes next run).

- **AM-11 — Dual-flavor branching on `destructive`.** `CheckpointCleanupBlobsSummary` carries both flavor key sets. The template branches on `summary.blobs.destructive === true`:
  - `destructive:false` (or absent — dry-run flavor): render `would_delete_count` + `would_free_bytes` + "Would delete" labels.
  - `destructive:true`: render `deleted` + `bytes_freed` + "Deleted"/"Freed" labels.
  The component helpers `blobCountFor` and `blobBytesFor` above centralize the branching so the template stays a one-line `{{ blobCountFor(...).label }}: {{ blobCountFor(...).value }}`.

- **AM-10/A-3 — `skipped[]` render + reason badge map + `ERROR:*` fallback + `skipped_truncated` notice.** When `lastDryRun().skipped.length > 0` (or `summary.blobs.skipped.length > 0` in a run result), the dry-run / run-result card renders:
  - A summary line: **"N pairs skipped — fail-safe"** (where N = `skipped.length`).
  - If `skipped_truncated === true`, an inline notice: **"(showing first 1000; more were truncated)"**.
  - A `<details>` block listing each entry: `{thread_id, checkpoint_ns, reason}` with the reason rendered via the badge map (helper `skippedReasonLabel` above): known reasons (`ZERO_REFS_FAIL_SAFE`, `MAX_REFS_EXCEEDED`) get human labels + styled chips; `ERROR:<ExceptionName>` renders generically as "Error: <name>" with an error-tone chip.
  - `skipped[]` is **informational only** — it does NOT affect the `expected_bytes` echo (AM-3 scope pin stays byte-equality; skipped pairs contribute 0 bytes to both runs).

- **AM-1 — Origin header handling.** **The FE sends NO special headers.** Same-origin SPA + localhost-family dev origins are auto-trusted by the BE's `require_trusted_origin` guard (`architecture-recommendation.md` Focus Area 1). Do NOT attempt `fetch(url, { mode: 'no-cors' })` or other tricks to "hide" the request — that hides the response body too, and we need `details.run_id` on 409-adoption. A Playwright cross-origin 403 test (T7.2) covers the guard's denial path.

- **Confirm flow payload echo**: the dialog's `message` string MUST echo `expected_bytes` (formatted as MB) from the most recent dry-run AND the count of excess checkpoint rows. **AM-16 honest-duration copy**: the dialog message MUST NOT promise seconds — dry-run is minutes-scale on a 33 GB production daemon. Excerpt:
  ```ts
  message: `This will permanently delete ~${formatBytes(dryRun.would_free_bytes)} of unreferenced blobs and ${dryRun.would_delete.checkpoint_rows} excess checkpoint rows. This may take several minutes on large databases. This cannot be undone.`,
  ```

- **Stale dry-run handling (AM-16 honest-freshness)**: before opening the dialog, check `new Date(dryRun.fresh_until).getTime() > Date.now()`. If stale, do NOT open the dialog — instead, surface a snack-bar: `"Dry-run is stale — re-run the check before executing."` AND highlight the dry-run button with a warning border (border-color: `$accent-rose`). **Honest copy caveat (AM-16)**: the 5-min freshness window may legitimately expire on slow disks where dry-run takes minutes itself — the message is the operator's prompt to re-run, NOT a defect signal. **[R-23, v3 fix pass]** the wording aligns to phase1's seed-old-row proof (test 24): staleness is age-vs-persisted-`started_at` on the SERVER's stored dry-run row — copy must not imply clock skew, client/server clock drift, or a server defect; it describes exactly the condition phase1 proves by seeding an old row.

- **Poll lifecycle**: `pollSub` is unsubscribed in `ngOnDestroy` AND inside the next `onExecute` call (prevent two polls stacking if the operator double-clicks — the lock + 409-adoption path is the BE side, but FE must not pile on). Use `takeUntilDestroyed(this.destroyRef)` on the initial `fetchStatus` subscription so deep-link → leave-page tears down cleanly. **AM-6 terminal statuses**: `pollRun` now terminates on `succeeded | failed | interrupted` — the component renders the appropriate result-panel variant for each.

- **Error → UI mapping** (**11 codes** — was 10 at v3, 8 in the v1 draft) [CF-6 doc-repair, v3.1]:
  - `run_in_flight` (409): **AM-14 — adopt `details.run_id`, resume polling, NO error toast.** The double-click + network-retry classes dissolve into "ride along" UX. Only fall through to the snack-bar if `details.run_id` is absent (malformed body — defensive).
  - `dry_run_stale` / `dry_run_required` (400): snack-bar "Re-run the dry-run check before executing." Reset `lastDryRun` to null in the local UI signal so the user must re-run.
  - `byte_count_mismatch` (400): snack-bar "The dry-run result changed since you ran it — re-run and try again." Same UI reset.
  - `confirm_required` / `backend_unsupported` / `not_initialized` (400/503): render the structured error message inline under the action row (red text, 0.75rem, never silent).
  - `not_found` (404) on poll: snack-bar "The run record was not found — re-run from the status page." Stop polling.
  - `origin_not_trusted` (403, **AM-1 NEW**): render the structured error message inline with a one-line explanation: "This origin is not trusted for maintenance actions. If you're seeing this unexpectedly, file a ticket." The kill-switch and FE localStorage paths do NOT silence this — operators must see it.
  - `maintenance_disabled` (503, **AM-13 NEW**): render a global banner across the page top: "Maintenance endpoints are disabled (MAINTENANCE_ENDPOINTS_ENABLED=0). Restart the daemon with the flag enabled to use this section." Dismissible. Auto-dismisses on the next `/availability` probe returning `state: "ready"`.
  - `internal_error` (500, **[CF-6 doc-repair, v3.1]** — router catch-all per A-8): **generic affordance, no special banner** — snack-bar "Unexpected error — retry, or contact the operator if it persists" with the raw code rendered in the inline banner (`{{ err.error }}`); offers a retry (re-run the failed action). Never silently swallowed.

- **Display helpers** (pure functions, exposed as `protected` methods for template):
  - `formatBytes(n: number): string` — human-readable (KB/MB/GB, binary). Source-grep pin: `1024` divisor (not 1000).
  - `formatDuration(ms: number): string` — "1.8s" / "412ms" / "2m 14s" for minute-scale durations. Honest about minutes; never claims "X seconds" for known-minutes-scale ops.
  - `formatTimestamp(iso: string): string` — locale string (matches migration's `logTime` pattern, app.ts:242). The wire emits `+00:00` (BE convention; JS `Date` parses both `+00:00` and `Z` equivalently — A-7 confirmed, no special handling needed; just documented).

- **No background refresh loop** — the page only re-fetches status on `ngOnInit` and after a successful execute completes. Polling `/runs/{id}` is scoped to a single execute; do not poll `/status` on a timer (the contract is clear that polling `/runs/{id}` is the post-execute mechanism).

[OQ-§6.5 RESOLVED, AM-9] Per overview §6.5, `last_run` is the most recent `succeeded|failed` of `kind ∈ {auto, manual_execute}` ONLY. The UI renders a single `Last run` card with a small kind badge ("auto" / "manual"). `manual_dry_run` never surfaces here — dry-run history is queryable via `GET /runs/{id}` by ID (out of scope for v1 UI).

#### T5.2. Template — `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.html`

Five distinct UI regions, in this order. **AM-10/A-3 (skipped[] render + badge map + `skipped_truncated`), AM-11 (dual-flavor branch on `destructive`), AM-12 (display `expected_duration_ms_hint`), AM-6 (interrupted-state affordance), AM-13 (`maintenance_disabled` banner), AM-16 (honest duration copy + `fresh_until`).**

1. **Status card** (always visible; **AM-11 dual-flavor** branch on `summary.blobs.destructive`):
   ```html
   <section class="ck-card ck-status">
     <h3>Current configuration</h3>
     <dl>
       <dt>Keep N checkpoints per thread</dt><dd>{{ status()?.config.checkpoint_max_per_thread }}</dd>
       <dt>Cleanup interval</dt><dd>{{ status()?.config.cleanup_interval_hours }} hours</dd>
       <dt>Blob-prune dry-run default</dt><dd>{{ status()?.config.blob_prune_dry_run_env_default === '1' ? 'ON (safe)' : 'OFF (destructive)' }}</dd>
     </dl>

     @if (status()?.last_run; as last) {
       <h3>Last run</h3>
       <dl>
         <dt>Kind</dt><dd>{{ last.kind }}</dd>
         <dt>Completed at</dt><dd>{{ formatTimestamp(last.completed_at) }}</dd>
         <dt>Status</dt><dd>{{ last.status }}</dd>
         <!-- AM-11: dual-flavor branch on destructive — would_free_bytes (dry) vs bytes_freed (destructive) -->
         <dt>{{ last.summary.blobs.destructive ? 'Bytes freed' : 'Would free' }}</dt>
         <dd>{{ formatBytes(last.summary.blobs.destructive ? (last.summary.blobs.bytes_freed ?? 0) : last.summary.blobs.would_free_bytes) }}</dd>
         <dt>Duration</dt><dd>{{ formatDuration(last.summary.duration_ms) }}</dd>
       </dl>
       <!-- AM-10: skipped render on last_run summary -->
       @if (last.summary.blobs.skipped.length > 0) {
         <p class="ck-skipped-summary">
           {{ last.summary.blobs.skipped.length }} pairs skipped — fail-safe
           @if (last.summary.blobs.skipped_truncated) { <span class="ck-truncated-note">(showing first 1000; more were truncated)</span> }
         </p>
       }
     } @else {
       <p class="ck-muted">No prior cleanup runs.</p>
     }

     @if (status()?.in_flight; as inFlight) {
       <div class="ck-warning">
         <mat-icon>warning</mat-icon>
         A {{ inFlight.kind }} run is in flight (started {{ formatTimestamp(inFlight.started_at) }}).
         Dry-run and execute are disabled.
       </div>
     }
   </section>
   ```

2. **Dry-run card** (action + result; **AM-10 skipped[] + AM-12 fresh_until + AM-16 honest-duration copy**):
   ```html
   <section class="ck-card ck-dry-run">
     <h3>Dry-run check</h3>
     <p class="ck-muted">
       Compute what would be deleted without writing anything. Required before execute.
       <strong>May take several minutes</strong> on large databases.
     </p>
     <button mat-raised-button color="primary" (click)="onDryRun()" [disabled]="!canDryRun() || dryRunning()">
       @if (dryRunning()) { <mat-spinner diameter="20"></mat-spinner> Running... } @else { Dry-run check }
     </button>

     @if (lastDryRun(); as dry) {
       <dl class="ck-result">
         <!-- AM-11: dry-flavor keys (would_delete_count / would_free_bytes) -->
         <dt>Would delete (blobs)</dt><dd>{{ dry.would_delete_count }}</dd>
         <dt>Would free</dt><dd>{{ formatBytes(dry.would_free_bytes) }}</dd>
         <dt>Excess checkpoint rows</dt><dd>{{ dry.would_delete.checkpoint_rows }}</dd>
         <dt>Excess writes</dt><dd>{{ dry.would_delete.writes }}</dd>
         <dt>Scanned thread-ns pairs</dt><dd>{{ dry.scanned.thread_ns_pairs }}</dd>
         <dt>Duration</dt><dd>{{ formatDuration(dry.duration_ms) }}</dd>
         <!-- AM-16 honest copy: fresh_until is shown so the operator can see when the echo expires -->
         <dt>Fresh until</dt><dd>{{ formatTimestamp(dry.fresh_until) }}</dd>
       </dl>

       <!-- AM-10: skipped render — summary line + reason badge map + ERROR:* fallback + truncated notice -->
       @if (dry.skipped.length > 0) {
         <div class="ck-skipped">
           <p class="ck-skipped-summary">
             <mat-icon>verified_user</mat-icon>
             <strong>{{ dry.skipped.length }} pairs skipped — fail-safe</strong>
             @if (dry.skipped_truncated) { <span class="ck-truncated-note">(showing first 1000; more were truncated)</span> }
           </p>
           <details>
             <summary>Show details</summary>
             <ul class="ck-skipped-list">
               @for (entry of dry.skipped; track entry.thread_id) {
                 <li>
                   <code class="ck-thread-id">{{ entry.thread_id }}</code>
                   @if (entry.checkpoint_ns) { <span class="ck-ns">{{ entry.checkpoint_ns }}</span> }
                   <span class="ck-badge ck-badge-{{ skippedReasonLabel(entry.reason).tone }}">
                     {{ skippedReasonLabel(entry.reason).label }}
                   </span>
                 </li>
               }
             </ul>
           </details>
         </div>
       }

       <details><summary>Raw JSON</summary><pre>{{ dry | json }}</pre></details>
     }
   </section>
   ```

3. **Execute card** (action + confirm flow; **AM-12 expected_duration_ms_hint display**):
   ```html
   <section class="ck-card ck-execute">
     <h3>Cleanup now</h3>
     <p class="ck-warning-text">Destructive — permanently deletes unreferenced blobs and excess checkpoint rows.</p>
     <button mat-raised-button color="warn" (click)="onExecute()"
             [disabled]="!lastDryRun() || executing() || isRunInFlight()">
       @if (executing()) { <mat-spinner diameter="20"></mat-spinner> Executing... } @else { Cleanup now }
     </button>

     @if (executing()) {
       <mat-progress-bar mode="indeterminate"></mat-progress-bar>
       <p class="ck-muted">Polling run status. Run ID: <code>{{ activeRunId() }}</code></p>
       <!-- AM-12: friendly copy from 202 body's expected_duration_ms_hint -->
       @if (expectedDurationHintMs(); as hint) {
         <p class="ck-muted">Expected duration: ~{{ formatDuration(hint) }}</p>
       }
     }
   </section>
   ```

4. **Result panel** (visible after execute completes; **AM-6 interrupted-state + AM-11 dual-flavor**):
   ```html
   @if (lastExecuteResult(); as res) {
     <section class="ck-card ck-result">
       <h3>Run result — {{ res.status }}</h3>
       <dl>
         <dt>Run ID</dt><dd><code>{{ res.run_id }}</code></dd>
         <dt>Kind</dt><dd>{{ res.kind }}</dd>
         <dt>Started at</dt><dd>{{ formatTimestamp(res.started_at) }}</dd>
         <dt>Completed at</dt><dd>{{ formatTimestamp(res.completed_at) }}</dd>
         @if (res.summary; as s) {
           <!-- AM-11: dual-flavor branch on destructive -->
           @if (s.blobs.destructive) {
             <dt>Bytes freed</dt><dd>{{ formatBytes(s.blobs.bytes_freed ?? 0) }}</dd>
             <dt>Blobs deleted</dt><dd>{{ s.blobs.deleted ?? 0 }}</dd>
           } @else {
             <dt>Would free</dt><dd>{{ formatBytes(s.blobs.would_free_bytes) }}</dd>
             <dt>Would delete (blobs)</dt><dd>{{ s.blobs.would_delete_count }}</dd>
           }
           <dt>Checkpoint rows deleted</dt><dd>{{ s.checkpoint_rows.deleted }}</dd>
           <dt>Duration</dt><dd>{{ formatDuration(s.duration_ms) }}</dd>

           <!-- AM-10: skipped render on run summary (same pattern as dry-run) -->
           @if (s.blobs.skipped.length > 0) {
             <dt>Skipped pairs</dt>
             <dd>
               <span class="ck-skipped-summary">{{ s.blobs.skipped.length }} pairs skipped — fail-safe</span>
               @if (s.blobs.skipped_truncated) { <span class="ck-truncated-note">(showing first 1000; more were truncated)</span> }
             </dd>
           }
         }
         @if (res.error; as err) {
           <dt class="ck-error">Error</dt><dd class="ck-error"><code>{{ err.code }}</code>: {{ err.message }}</dd>
         }
       </dl>

       <!-- AM-6: interrupted-state affordance — re-run to converge -->
       @if (canRerunInterrupted(res)) {
         <div class="ck-interrupted-card">
           <mat-icon>schedule</mat-icon>
           <div>
             <strong>Daemon restarted mid-run</strong>
             <p>Prune is retention-idempotent — re-running converges the remaining work. No cancel button in v1.</p>
           </div>
           <button mat-raised-button color="primary" (click)="onExecute()">Re-run</button>
         </div>
       }

       <details><summary>Raw JSON</summary><pre>{{ res | json }}</pre></details>
     </section>
   }
   ```

5. **Inline error banner** (visible when `lastError` is non-null; **AM-13 maintenance_disabled global banner**):
   ```html
   <!-- AM-13: kill-switch OFF → global banner. Sits above all cards; auto-dismisses on next /availability returning state:ready. -->
   @if (lastError()?.error === 'maintenance_disabled') {
     <section class="ck-card ck-banner ck-banner-disabled">
       <mat-icon>block</mat-icon>
       <div>
         <strong>Maintenance endpoints are disabled</strong>
         <p>The daemon was started with MAINTENANCE_ENDPOINTS_ENABLED=0. Restart with the flag enabled to use this section.</p>
       </div>
       <button mat-button (click)="service.lastError.set(null)">Dismiss</button>
     </section>
   }

   @if (lastError(); as err) {
     <section class="ck-card ck-error-banner">
       <mat-icon>error_outline</mat-icon>
       <div>
         <strong>{{ err.error }}</strong>
         <p>{{ err.message }}</p>
         <!-- AM-1: origin_not_trusted gets a one-line guidance note -->
         @if (err.error === 'origin_not_trusted') {
           <p class="ck-error-note">This origin is not trusted for maintenance actions. If you're seeing this unexpectedly, file a ticket.</p>
         }
       </div>
       <button mat-button (click)="service.lastError.set(null)">Dismiss</button>
     </section>
   }
   ```

   **Note:** the `run_in_flight` case is intentionally NOT shown here — by the time the error reaches the banner, `adoptRunIdFromError` has already adopted the run_id and polling has started. If `details.run_id` is absent (defensive fallthrough), the inline banner renders as above.

#### T5.3. Styles — `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.scss`

Scaffold after `frontend/src/app/components/migration/migration.component.scss`. Reuse the same color vars (`$bg-primary`, `$bg-card`, `$border-color`, `$accent-cyan`, `$accent-rose` for destructive). Card pattern mirrors `migration.component.scss` `.migration-card` / `.state-card`. **AM-14 / AM-10 additions**: classes for the skipped badge map (`ck-badge.ck-badge-safe` / `.ck-badge-limit` / `.ck-badge-error` — three tones for `ZERO_REFS_FAIL_SAFE`, `MAX_REFS_EXCEEDED`, `ERROR:*`), the truncated notice (`.ck-truncated-note`), and the interrupted card (`.ck-interrupted-card`). Class names mirror those used in T5.2 template. **Strict budget**: stay well under 16kB (8kB warning) so we never bump the 24kB cap if a future patch adds more state branches.

**Acceptance** (T5 overall):
- Page renders 4 cards (status, dry-run, execute, error) + result panel when applicable.
- Dry-run button → click → spinner → result renders with formatted bytes/duration.
- Stale dry-run (>5 min): execute button disabled AND tooltip "Re-run dry-run first". Honest copy: "May take several minutes on large databases" surfaced in the dry-run muted text.
- Execute button → confirm dialog with destructive styling → confirm → 202 received → `expected_duration_ms_hint` displayed → poll starts → result renders.
- All **11** `MaintenanceErrorCode` values from the v3 error table map to a visible UI affordance (snack-bar OR inline banner) [CF-6 doc-repair, v3.1]; `run_in_flight` is silently absorbed by 409-adoption (no error toast); `maintenance_disabled` gets a global banner above all cards; `internal_error` gets the generic retry affordance.
- Skipped pairs render with summary line + per-entry reason badge (known codes + `ERROR:*` fallback) + truncated notice when applicable.
- Interrupted-state result renders the "daemon restarted mid-run — re-run to converge" affordance card with a re-run button. No cancel button anywhere.
- `tsc --noEmit -p tsconfig.app.json` passes.
- `npm run build` (production) passes WITHOUT any budget warning.

**Commit slice**: `feat(checkpoint-cleanup): add checkpoint cleanup section component`.

---

### T6. Jest Logic-Mirror Tests — pure spec files, no TestBed

**Owner**: any FE worker. **Depends on**: T1 + T5. **AM-16 amendments**: 409-adoption spec, skipped-render spec, state-enum gating pins, no-idempotency pin, 11-code error rendering *(10→11 [CF-6 doc-repair, v3.1])* , pollRun terminates on `interrupted`.

**Goal**: behavioral + source-grep pins that catch drift before it lands. House style: plain TS + hand-rolled mocks, mirrors `frontend/src/app/services/instance.service.spec.ts:14-35` and `frontend/src/app/pages/jobs/jobs-page.bindings.pins.spec.ts`.

#### T6.1. Service spec — `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.service.spec.ts`

Pure spec — instantiate the REAL service with a stub `HttpClient` whose methods return pre-canned `Observable`s (no TestBed). Pattern: copy `MockApiService` from `instance.service.spec.ts:14-35`.

Coverage:
1. `fetchAvailability()` hits `GET /api/maintenance/checkpoint-cleanup/availability` and updates the `availability` signal. **AM-14** — assert the returned object has `state: 'ready'` (the contract shape).
2. `fetchStatus()` hits `GET /api/maintenance/checkpoint-cleanup/status` and updates the `status` signal. Fixture includes `last_run` with `summary.blobs.skipped` array + `summary.blobs.destructive: false` (dry flavor).
3. `dryRun()` hits `POST /api/maintenance/checkpoint-cleanup/dry-run` with empty body `{}` and updates `lastDryRun`. **AM-10** — fixture includes `skipped: [{thread_id, checkpoint_ns, reason: 'ZERO_REFS_FAIL_SAFE'}]`; assert the signal's value carries the skipped entries.
4. `execute({...})` hits `POST /api/maintenance/checkpoint-cleanup/execute` with the EXACT v2 payload shape — `assert exact field order in the wire body via `expect(httpMock.post).toHaveBeenCalledWith(url, body)`. **AM-17** — assert the body has EXACTLY `{dry_run_run_id, expected_bytes, confirm: true}` and does NOT contain `idempotency_key` (order pin + negative-pin on the dropped field). Fixture 202 response includes `advisory: null` + `expected_duration_ms_hint: 412` (AM-12).
5. `getRun(runId)` hits `GET /api/maintenance/checkpoint-cleanup/runs/{run_id}` (path interpolation pin). Fixture can return `status: 'interrupted'` to exercise the AM-6 terminal branch.
6. `pollRun()` emits at intervals, STOPS on `succeeded` (use jest fake timers), STOPS on `failed`, **STOPS on `interrupted`** (AM-6), surfaces 404 via `lastError`.
7. **Error mapping**: `toErrorBody({ status: 409, error: { error: 'run_in_flight', details: { run_id: 'ckpt-...' } } })` returns the structured body with the correct `error` code AND `details.run_id`.
8. **Error fallback**: `toErrorBody({ status: 0, error: null })` returns `{ error: 'unknown', message: '...' }` — does NOT throw.
9. **AM-14 — `adoptRunIdFromError`**: with body `{error: 'run_in_flight', details: {run_id: 'ckpt-...' }}` returns the `run_id` string. With body `{error: 'run_in_flight'}` (no details) returns `null`. With body `{error: 'byte_count_mismatch'}` returns `null`. With body `null` returns `null`.
10. **[R-17, v3 fix pass] `POLL_MAX_DURATION_MS` hard-timeout coverage**: the service exposes `static readonly POLL_MAX_DURATION_MS` (10 min, per PR-2) and the spec proves (fake timers) that a run still `'running'` after the max duration terminates the poll AND surfaces the inline "still in progress" error — the timeout is not just a risk-table idea, it is spec-covered in T6.1.

#### T6.2. Section component spec — `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.spec.ts`

Mirror `frontend/src/app/pages/jobs/jobs.component.spec.ts:122-168` (`mockDialog` + `MockDialogRef` pattern). Use a `MockCheckpointCleanupService` exposing the same signal surface as the real service. **AM-16 amendments**: 409-adoption test, skipped[] render test, state-enum gating test, no-idempotency-key test, interrupted-state render test, 11-code error rendering test *(10→11 [CF-6 doc-repair, v3.1])*.

Coverage:
1. **Status render**: with `service.status` set to a fixture, the dl/dt/dd pairs render with the right values. **AM-11** — covers BOTH flavors: a `destructive: false` last_run shows "Would free" + `would_free_bytes`; a `destructive: true` last_run shows "Bytes freed" + `bytes_freed`.
2. **Dry-run button enabled-state transitions**: `canDryRun()` returns false → button disabled; service flips to eligible → button enabled.
3. **Confirm dialog wired** (mirrors `jobs.component.spec.ts:405,642-647`): push `mockDialog.nextResult = true`, click execute → assert `mockDialog.openCalls[0].component === ConfirmDialogComponent` AND `mockDialog.openCalls[0].data.destructive === true` AND `panelClass: 'dark-modal-panel'` is applied.
4. **Confirm cancel**: `nextResult = false` → execute is NOT called (`service.execute` jest.fn never invoked).
5. **AM-17 — Confirm confirm** (no idempotency_key): `nextResult = true` → `service.execute` called with EXACTLY `{dry_run_run_id, expected_bytes, confirm: true}` (assert EXACTLY these three fields, no `idempotency_key`, no `idempotencyKey`, no `uuid`; pin `confirm: true` literal; assert `Object.keys(executeArgs).sort() === ['confirm', 'dry_run_run_id', 'expected_bytes']`).
6. **Confirm dialog echo message**: the dialog's `data.message` string contains BOTH the formatted byte count AND the checkpoint-row count from `lastDryRun.would_delete` (regression guard against "we forgot to echo and the operator clicks blind"). **AM-16** — message contains honest-duration copy ("several minutes" or "may take" — pin a substring the implementer chooses at coding time).
7. **Stale dry-run short-circuit**: with `fresh_until` in the past, clicking execute opens NO dialog and surfaces the snack-bar (mock snackbar records the open).
8. **AM-16 — Error rendering per stable code** (**11 codes now** — was 10 at v3, 8 in the v1 draft [CF-6 doc-repair, v3.1]): for each `MaintenanceErrorCode` (`not_initialized`, `run_in_flight`, `dry_run_stale`, `byte_count_mismatch`, `not_found`, `origin_not_trusted`, `maintenance_disabled`, `internal_error`, …), set `service.lastError` to a fixture and assert the inline banner renders with the right `error` text + dismiss button clears the signal. `internal_error` renders generically (code + message + retry affordance — no special banner). **Special case for `maintenance_disabled`**: assert the global banner (`.ck-banner-disabled`) renders ABOVE the cards. **Special case for `origin_not_trusted`**: assert the one-line guidance note renders.
9. **Poll start/stop**: after execute 202, `service.pollRun` is called with the `run_id` AND a `takeUntilDestroyed` subscription exists; simulate a `succeeded` response and assert `executing()` flips back to false.
10. **AM-6 — Interrupted poll termination**: pollRun emits a `status: 'interrupted'` fixture; assert `executing()` flips to false AND the result panel renders the `.ck-interrupted-card` with the re-run button.
11. **AM-14 — 409-adoption behavior**: simulate `service.execute` throwing with `lastError.error === 'run_in_flight'` AND `details.run_id = 'ckpt-...'`. Assert: `service.adoptRunIdFromError` was called AND `service.pollRun('ckpt-...')` was called AND `executing()` is true AND the snack-bar was NOT opened.
12. **AM-14 — 409 fallthrough (malformed body)**: simulate `lastError.error === 'run_in_flight'` with NO `details.run_id`. Assert: `executing()` is false AND the inline error banner renders the standard error.
13. **AM-10 — Skipped render**: with `lastDryRun.skipped = [{thread_id, checkpoint_ns, reason: 'ZERO_REFS_FAIL_SAFE'}, {thread_id, checkpoint_ns, reason: 'MAX_REFS_EXCEEDED'}, {thread_id, checkpoint_ns, reason: 'ERROR:MyException'}]`, assert the summary line "3 pairs skipped — fail-safe" renders AND each entry's reason renders via the badge map (3 badges with tones `safe`, `limit`, `error`).
14. **AM-10 — Skipped truncated notice**: with `lastDryRun.skipped_truncated: true`, assert the inline truncated note renders.
15. **AM-11 — Dual-flavor in result panel**: result panel with `summary.blobs.destructive: false` shows "Would free" + `would_free_bytes`; same with `destructive: true` shows "Bytes freed" + `bytes_freed`.
16. **AM-12 — expected_duration_ms_hint display**: with `expectedDurationHintMs` set (from a 202), assert the executing card shows "Expected duration: ~X" copy.
17. **Destroy tears down poll**: set `executing = true`, call `ngOnDestroy` on the testable mirror, assert `pollSub.unsubscribe` was called (mirror-parity assertion, same convention as `jobs.component.spec.ts` mock-job-controller wiring).

#### T6.3. Source-grep pins — co-located with each component spec

Pattern from `frontend/src/app/pages/jobs/jobs-page.bindings.pins.spec.ts:380-409` — read the production source via `fs.readFileSync` and `expect(componentSrc).toMatch(/.../)`. **AM-16 amendments**: replace `idempotency-key-present` with `no-idempotency-key` (AM-17 DROPPED), update `error-code-union-exhaustive` count from 8 to 10 (at v3; **11 since v3.1** [CF-6 doc-repair, v3.1]), add new pins for state-enum gating, 409-adoption, skipped-render, interrupted-state render. **[R-2, v3 fix pass] Pin table total = 15 source-grep pins** (the 14 AM-16-era pins + the `sections-registry-load-bearing` pin [R-addition]). **[v3.2, A+C ratified] Pin table total = 18** — +3 dry-run-projection pins (`projection-fields-render`, `confirm-message-journey-copy`, `run-again-banner-when-projection-nonzero`; see §v3.2 FE Work Block). **18 is THE count every count site agrees on** (was 15 at v3/v3.1).

| Pin | What it pins | Failure mode it catches |
|---|---|---|
| `confirm-dialog-wired` | Production source contains `this.dialog.open(ConfirmDialogComponent` AND the open call passes `panelClass: 'dark-modal-panel'` AND the close site uses `afterClosed().subscribe` | A developer removes the panelClass (dark theme breaks) or hoists the execute call above the dialog open (destructive without confirm) |
| `poll-stop-on-terminal` | `service.pollRun(` exists, AND the returned observable is subscribed via `takeUntilDestroyed`. **[R-13, v3 fix pass — wording aligned to T5.1/T6.2 behavior]** the pin ALSO asserts the terminal predicate (the poll source completes on `status ∈ {succeeded, failed, interrupted}` — T6.1 cases 6 + T6.2 case 10) so the pin's name matches what it proves; `takeUntilDestroyed` alone tests teardown, not stopping. | A developer drops `takeUntilDestroyed` (leak) OR breaks the terminal-stop predicate (poll runs forever) |
| `no-idempotency-key` (**AM-17 — REPLACES** `idempotency-key-present`) | (a) Production source does NOT contain `crypto.randomUUID` in the execute flow AND (b) the literal `idempotency_key` does NOT appear in the execute payload object AND (c) `CheckpointCleanupExecuteRequest` model does NOT have an `idempotency_key` field | A developer adds the dropped field back (it would deadlock the contract) |
| `byte-echo-in-confirm-message` | Production source's confirm-dialog `data.message` string template contains BOTH `formatBytes(` AND `would_delete.checkpoint_rows` (or `would_delete.writes`) | Operator confirms blind — the "echo" guard regresses |
| `error-code-union-exhaustive` | **AM-13/AM-1/A-8 — count pin is 11** (was 10 at v3, 8 in the v1 draft) [CF-6 doc-repair, v3.1]. The pin asserts the handling set against `MAINTENANCE_ERROR_CODES` (the as-const tuple — T1.1): all 11 codes (`not_initialized`, `not_found`, `run_in_flight`, `confirm_required`, `dry_run_required`, `dry_run_stale`, `byte_count_mismatch`, `backend_unsupported`, `origin_not_trusted`, `maintenance_disabled`, `internal_error`). Acceptable: a fallthrough "default" branch that surfaces the code as text. | A new code is added server-side and FE silently ignores it |
| `poll-interval-default-2000` | `POLL_INTERVAL_MS = 2000` exists as a `readonly` static constant on `CheckpointCleanupService` AND the `pollRun(` call site uses that constant (or the default arg in the service signature is `CheckpointCleanupService.POLL_INTERVAL_MS`). **AM-14 — pin is on the constant name, not the literal**, so re-skinning the value at a single site can't slip through. **[R-17, v3 fix pass]** the pin ALSO greps every `pollRun(` CALL SITE (component, specs) for the constant — a hardcoded `2000` at a call site fails the pin. | The poll tightens (BE load) or loosens (UX lag) silently; a call-site hardcoded literal drifts from the constant |
| `format-bytes-uses-binary` | `formatBytes(` source contains `1024` (binary units) — guards against accidentally switching to decimal SI units which would mislead operators | "Your 1GB run freed 0.93 GiB" — off by ~7% forever |
| `state-enum-gating` (**AM-14 NEW**) | Gear-menu probe source contains `state === 'ready'` (exact equality, not `data.eligible`) AND `app.routes.ts` guard source contains `state === 'ready'` | A developer branches on `eligible` instead of the enum; or the route guard forgets the state check and stale FE dist + missing BE router 404s into SPA fallback |
| `409-adoption-wired` (**AM-14, AM-17 NEW**) | Component source contains `adoptRunIdFromError(` AND a `run_in_flight` branch calls `service.pollRun(` with the adopted run_id AND does NOT call `snackBar.open` in that branch | The double-click / network-retry class regresses to a visible error toast |
| `skipped-render-wired` (**AM-10 NEW**) | Template source contains `skipped.length` (the summary line check) AND `skippedReasonLabel(` (the badge map) AND `skipped_truncated` (the truncation notice) | A developer removes the skipped render — operators lose visibility into fail-safe pairs |
| `interrupted-affordance` (**AM-6 NEW**) | Component source contains `canRerunInterrupted(` (or `status === 'interrupted'`) AND template contains `ck-interrupted-card` AND does NOT contain any `cancel` button in the execute flow | A cancel button appears (out of scope v1) or the re-run affordance is dropped |
| `dual-flavor-branch` (**AM-11 NEW**) | Component source contains `blobs.destructive` (template branches on the boolean) AND `would_free_bytes` + `bytes_freed` appear as alternative reads | A developer hardcodes one flavor and the other surface silently mis-renders |
| `expected-duration-hint` (**AM-12 NEW**) | Component source contains `expectedDurationHintMs` (the signal set from the 202 body's `expected_duration_ms_hint`) AND template renders it in the executing card | The 202 hint drops on the floor and operators see only an indeterminate spinner |
| `fresh_until-shown` (**AM-16 NEW**) | Template source contains `formatTimestamp(dry.fresh_until)` (or equivalent — the dry-run result panel surfaces the freshness window so operators see when the echo expires) | The 5-min window expires silently and operators re-run execute blind |
| `sections-registry-load-bearing` (**[R-addition], v3 fix pass NEW**) | `maintenance.component.ts` source declares the `sections` registry array AND `maintenance.component.html` renders via `@for (section of sections; ...)` + `*ngComponentOutlet="section.component"` — the Maintenance page MUST render its sections FROM the registry array (user requirement #2 extensibility: the registry is load-bearing, not decorative). Negative arm: adding a second hard-coded `<app-checkpoint-cleanup>` outside the `@for` fails the pin. | A developer bypasses the registry with a hard-coded section tag — the page still works but extensibility silently dies |

#### T6.4. Verify loop (mandatory per house style)

Run BEFORE every commit that touches `frontend/`:

```bash
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble/frontend
npx tsc --noEmit -p tsconfig.app.json    # compile check
npx jest src/app/pages/maintenance/     # the new specs
npm run build                            # full prod build — proves budget headroom
```

The `npm run build` step is NOT optional — it's the only check that catches the anyComponentStyle 24kB error budget. If T5.3 scss grows past 16kB, the build fails loudly.

**Commit slice**: `test(checkpoint-cleanup): add logic-mirror specs and source-grep pins`.

---

### T7. Playwright E2E — `frontend/e2e/maintenance-checkpoint-cleanup.spec.ts`

**Owner**: any FE worker. **Depends on**: T1–T5 merged (T6 specs run in `npm test`, T7 in `npm run e2e`). **AM-16 amendments**: 409-adoption spec (no error toast), cross-origin 403 spec, kill-switch hide spec, state-enum menu gating, dual-flavor branch coverage, skipped-render spec, interrupted-state render spec (if cheap). **[R-11, v3 fix pass]** the Playwright run uses a **dedicated port** for the e2e daemon (NOT the developer's :8079 — e.g. `:8099` via a Playwright-project-scoped `webServer` env), **`reuseExistingServer: false`** for this project (a reused foreign daemon would point the destructive spec at an unknown DB), and a **daemon canary** before any destructive test (spec boots by asserting `/availability` returns `state:'ready'` AND `ENSEMBLE_DB_DSN` resolves to the disposable DB — refuse otherwise). Spec total = **14 Playwright test cases** [R-2].

**Goal**: e2e validates the full UI flow against a disposable-PG dev daemon. **MUST NOT** touch `ensemble_prod`.

#### T7.1. Env-guard — refuse to run against prod

Top of the spec file (before any `test()` block):

```ts
import { test, expect } from '@playwright/test';

const DB_DSN = process.env.ENSEMBLE_DB_DSN ?? '';
test.beforeAll(() => {
  if (!DB_DSN) throw new Error('ENSEMBLE_DB_DSN must be set to a disposable PG DSN for this spec.');
  if (/ensemble_prod/i.test(DB_DSN)) {
    throw new Error('REFUSING to run destructive e2e against ensemble_prod. Use a disposable PG DSN.');
  }
});
```

The playwright `webServer` array (`frontend/playwright.config.ts`) auto-boots `bash dev.sh` on port 8079. The CI runner (or local developer) must set `ENSEMBLE_DB_DSN` in the environment before running `npx playwright test`. The spec ALSO scrubs `POSTGRES_*` env at the top so an accidental `POSTGRES_DB=ensemble_prod` doesn't bleed through (matches `tests/helpers/checkpoint_prune_pg.py` convention).

#### T7.2. Spec outline

```ts
test.describe('Maintenance — Checkpoint Cleanup', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/maintenance/checkpoint-cleanup');
  });

  test('gear menu shows Maintenance when /availability reports state=ready', async ({ page }) => {
    // AM-14: state-enum gating. intercept GET /api/maintenance/checkpoint-cleanup/availability
    // → 200 { eligible: true, state: 'ready', backend: 'postgres', reason: null }
    // (a) navigate to /
    // (b) open gear menu (click [aria-label="Settings menu"])
    // (c) assert menu item with text "Maintenance" exists and points at /maintenance/checkpoint-cleanup
  });

  test('gear menu hides Maintenance when /availability reports state=backend_unsupported', async ({ page }) => {
    // AM-14: state-enum gating hides on non-ready states. intercept availability
    // → 200 { eligible: false, state: 'backend_unsupported', backend: 'sqlite', reason: 'blob_prune_postgres_only' }
    // assert NO "Maintenance" item in the gear menu
    // AM-14 note: must branch on `state`, not `eligible` — covered by the source-grep pin.
  });

  test('gear menu hides Maintenance when /availability reports state=kill_switched (AM-13)', async ({ page }) => {
    // boot dev daemon with MAINTENANCE_ENDPOINTS_ENABLED=0 (env-guard above does not cover
    // this; it's a Playwright project config or a separate worker). intercept availability
    // → 200 { eligible: false, state: 'kill_switched', backend: 'postgres', reason: 'MAINTENANCE_ENDPOINTS_ENABLED=0' }
    // assert NO "Maintenance" item in the gear menu
    // assert NO error toast (the spec says hide, not error)
  });

  test('page renders status card with current config (dual-flavor branch coverage)', async ({ page }) => {
    // AM-11: two sub-cases — (a) last_run summary.blobs.destructive:false shows
    // "Would free" + would_free_bytes; (b) destructive:true shows "Bytes freed" + bytes_freed.
    // intercept status → 200 fixture (one per flavor).
    // assert dl pairs "Keep N checkpoints per thread" = 3, "Cleanup interval" = "24 hours"
    // assert the flavor-appropriate label + value renders
  });

  test('page renders last-run summary with skipped badge when present', async ({ page }) => {
    // AM-10: last_run summary.blobs.skipped > 0 → render summary line + badge map
    // intercept status with last_run fixture (kind: 'auto', succeeded, summary.blobs.skipped
    // contains ZERO_REFS_FAIL_SAFE + MAX_REFS_EXCEEDED + ERROR:MyException)
    // assert "Last run" h3, "Kind" = auto, bytes-formatted text appears
    // assert "3 pairs skipped — fail-safe" summary line renders
    // assert each reason renders with the appropriate badge tone
  });

  test('dry-run click shows results + skipped badge + fresh_until', async ({ page }) => {
    // AM-10 + AM-16: dry-run response includes skipped[] + fresh_until + honest-duration copy
    // intercept POST /dry-run → 200 fixture with skipped + fresh_until 6 minutes in the future
    // click "Dry-run check"
    // assert dl pairs render with formatted bytes + counts
    // assert "N pairs skipped — fail-safe" line + skipped detail <details>
    // assert "Fresh until" line shows the formatted ISO timestamp
    // assert muted copy says "May take several minutes on large databases"
    // assert raw JSON <pre> is present
  });

  test('execute confirm flow — cancel does not POST', async ({ page }) => {
    // intercept POST /execute → assert never called
    // intercept POST /dry-run → fixture
    // click dry-run → click execute → click "Cancel" in dialog
    // assert POST /execute was NOT made
  });

  test('execute confirm flow — confirm POSTs and polls to terminal (no idempotency_key)', async ({ page }) => {
    // AM-17: assert NO idempotency_key field in the POST body
    // intercept dry-run → fixture with bytes=268435456
    // intercept POST /execute → 202 { run_id: 'ckpt-…', advisory: null, expected_duration_ms_hint: 412 }
    // intercept GET /runs/{run_id} → first emit 'running', second emit 'succeeded' with summary
    // click dry-run → click execute → click "Cleanup now" in dialog
    // assert POST /execute called with body containing EXACTLY {dry_run_run_id, expected_bytes, confirm: true}
    //    — assert the request body has no `idempotency_key` field (key check)
    // assert "Expected duration: ~X" copy appears in the executing card (AM-12)
    // assert "Run result — succeeded" card renders
    // assert "Bytes freed" line shows the formatted bytes (destructive flavor — AM-11)
  });

  test('stale dry-run surfaces re-run prompt and does not POST execute', async ({ page }) => {
    // AM-16: dry-run may legitimately expire on slow disks. intercept dry-run with fresh_until = 1 minute ago.
    // click dry-run → click execute → assert NO dialog opens AND snack-bar shows "stale"
  });

  test('409 run_in_flight from execute ADOPTS run_id and resumes polling (no error toast)', async ({ page }) => {
    // AM-14 + AM-17: 409-adoption. intercept dry-run → fixture.
    // intercept POST /execute → 409 { error: 'run_in_flight', details: { run_id: 'ckpt-…', started_at: '...' } }
    // intercept GET /runs/{ckpt-…} → emit 'running', then 'succeeded'
    // click dry-run → click execute → click "Cleanup now"
    // assert: NO error banner / toast appears
    // assert: poll begins against GET /runs/{ckpt-…} (assert the request fires)
    // assert: result panel renders with "Run result — succeeded"
  });

  test('interrupted-state run result renders "re-run to converge" affordance', async ({ page }) => {
    // AM-6: boot-sweep CAS'd a stale running row. intercept dry-run → fixture.
    // intercept POST /execute → 202; intercept GET /runs/{run_id} → 'interrupted' (single terminal emission)
    // click dry-run → click execute → click "Cleanup now"
    // assert: result panel renders "Run result — interrupted"
    // assert: the .ck-interrupted-card renders with "Daemon restarted mid-run" copy + a Re-run button
    // assert: NO cancel button anywhere
  });

  test('cross-origin request to /dry-run is rejected with 403 origin_not_trusted', async ({ browser }) => {
    // AM-1 + AM-16: launch a Playwright context with extraHTTPHeaders: { Origin: 'http://evil.example' }
    // navigate the context to /maintenance/checkpoint-cleanup
    // intercept the dry-run request and assert: it returned 403 { error: 'origin_not_trusted' }
    // the FE cannot read the response body (CORS preflight fails) — the test fires from inside
    // the FE dev server to exercise the BE guard. Confirms the BE refuses cross-origin.
    // [R-12, v3 fix pass] fire the refusal via `context.request.post('/api/maintenance/checkpoint-cleanup/dry-run',
    // { headers: { Origin: 'http://evil.example' } })` — the API-level request context BYPASSES the CORS
    // read-blocking, so the test can ASSERT THE 403 BODY (`error === 'origin_not_trusted'`, structured
    // `{error, message}` shape) — not just the status code. Status-only assertion is insufficient:
    // the body shape IS the contract surface the guard refuses with.
  });

  test('origin_not_trusted response renders the inline guidance note', async ({ page }) => {
    // AM-1: simulate the FE somehow seeing the 403 body (e.g. test page in same-origin so the
    // guard allows it through, then we force the body via a mock). Or: use page.route to fake the
    // BE response to a same-origin /dry-run POST as 403 origin_not_trusted and verify the FE banner.
    // assert: the inline error banner shows "origin_not_trusted" + the one-line guidance note
    //   "This origin is not trusted for maintenance actions. ..."
  });

  test('maintenance_disabled response renders the global banner', async ({ page }) => {
    // AM-13: boot dev daemon with MAINTENANCE_ENDPOINTS_ENABLED=0; assert /availability returns
    // state:kill_switched (gear menu hides via AM-14 — covered by an earlier test). For this test,
    // use page.route to mock a same-origin POST /execute returning 503 maintenance_disabled.
    // assert: the global .ck-banner-disabled renders ABOVE all cards with the kill-switch copy.
  });
});
```

#### T7.3. Steps skipped when BE is ineligible

If the boot probe `GET /availability` returns a non-`ready` state OR returns 503, the spec files `test.skip()` for the destructive-flow tests. This is a runtime check inside `test.beforeEach`:

```ts
test.beforeEach(async ({ page }) => {
  const resp = await page.request.get('/api/maintenance/checkpoint-cleanup/availability');
  if (resp.status() !== 200 || (await resp.json()).state !== 'ready') {
    test.skip(true, 'Maintenance backend not eligible on this dev daemon — skipping destructive-path e2e.');
  }
  await page.goto('/maintenance/checkpoint-cleanup');
});
```

The non-destructive tests (gear menu visibility, status render, dry-run render) still run on SQLite daemons — they validate that the menu HIDES on SQLite (`state: 'backend_unsupported'`), that the page renders the status card even when `in_flight` is non-null, etc. This matches the overview §Test Strategy → e2e "validates" list. The cross-origin 403 test uses a separate browser context and runs against any healthy daemon.

#### T7.4. e2e run command

```bash
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble/frontend
ENSEMBLE_DB_DSN='postgresql+psycopg://ens_test:ens_test@localhost:5433/ensemble_blob_prune_e2e_<uuid>' \
  npx playwright test maintenance-checkpoint-cleanup --workers=1
```

The disposable DB is created via `tests/helpers/checkpoint_prune_pg.py`'s pattern (per-test database, `DROP DATABASE WITH (FORCE)` on teardown). The webServer `bash dev.sh` will pick up the `ENSEMBLE_DB_DSN` from the env block.

**Acceptance** (T7 overall):
- Spec file fails FAST (before any `test()` runs) if `ENSEMBLE_DB_DSN` is unset or matches `ensemble_prod`.
- All non-destructive tests pass against a fresh disposable-PG dev daemon.
- All destructive tests pass against a dev daemon with 1+ unreferenced blob row (insert via the disposable-PG helper).
- The kill-switch hide test runs in a separate Playwright project with `MAINTENANCE_ENDPOINTS_ENABLED=0` set on the webServer env.
- The cross-origin 403 test uses a separate `browser.newContext({ extraHTTPHeaders: { Origin: 'http://evil.example' } })` — and fires the refusal via `context.request.post` with the explicit `Origin` header so the **403 BODY** (`origin_not_trusted`) is asserted, not just the status [R-12, v3 fix pass].
- CI runs `npx playwright test maintenance-checkpoint-cleanup` in the existing playwright job; this file does NOT alter the existing playwright config.

**Commit slice**: `test(e2e): add maintenance checkpoint-cleanup playwright spec with prod-refusal guard`.

---

## Activation Steps (Phase 3 contract for Phase 2's output)

These run AFTER T1–T7 merge. **Daemon rebuild does NOT ship the FE dist** (project critical-notes — the standing trap flagged across multiple activation-pending notes). The full sequence is:

1. **FE dist rebuild**: `cd frontend && npm run build` (or `make install` from the repo root, which wraps it). This produces `frontend/dist/`. **Confirm**: `ls -la frontend/dist/` shows a recent mtime. The build output's anyComponentStyle budget MUST be silent (no warnings).
2. **Daemon rebuild + restart**: `make install` (per project convention). The Makefile copies `frontend/dist/` into the daemon's static-file root.
3. **Kill-switch default (AM-13):** `MAINTENANCE_ENDPOINTS_ENABLED=1` is the **default** — no FE env var needed. The kill-switch is a BE env, read at boot. Operators flipping it OFF must restart the daemon; no FE-side `localStorage` mirror (architect ruling). Hide-not-error UX: gear menu entry simply disappears (T2), route falls through to `/` (T3), global banner appears for stale in-flight pages (T5).
4. **Live spot-check on disposable-PG dev daemon**: open `http://localhost:4199`, click the gear menu, click Maintenance, click Dry-run check, click Cleanup now → confirm → poll → result. Verify `/status` shows the new run row in `last_run`. **Then close the activation-pending notes.**

If the FE build's bundle budget trips the 6MB initial error, the activation is BLOCKED. Reduce by either lazy-loading the page (already done via `loadComponent`) or splitting the shared Material module imports (do NOT bring in heavy modules like `MatTableModule` for this page — we use raw `<dl>` and `<pre>`).

**⚠️ FE dist rebuild trap (AM-16 standing warning):** the FE changes in this PR (state-enum gating, 409-adoption wiring, skipped-render, dual-flavor branching, interrupted-state render) ONLY ship to operators when `frontend/dist/` is rebuilt AND the daemon is restarted. A daemon restart alone ships the BE changes but leaves operators looking at the OLD FE — gear-menu probe will not show Maintenance on a fresh daemon boot because the OLD FE still branches on `eligible` not `state === 'ready'`. Always verify `ls -la frontend/dist/` after `npm run build` before declaring activation complete.

---

## Open Questions affecting Phase 2 work items

Each item below is RESOLVED per `plan-overview.md` §Open Questions + the Contract-Feedback Register addendum. The "disposition" column cites the architect amendment (AM) that closed it. **No open questions remain for the FE work.** Items tagged `[OQ-§6.x]` in the task bodies above are kept for traceability — they all now point to closed dispositions.

| Open Q | Affected sub-tasks | Final disposition (post-AM) | What FE does |
|---|---|---|---|
| **§6.1** Error body shape | T5.1 error mapping, T6.2 error-rendering test | **RESOLVED — A-8 RATIFIED.** Structured dict (`plane.py:71-170` shape), binding for all 5 endpoints. Confirmed in error-code table. | T5.1 maps every error code to a visible UI affordance; T6.2 spec asserts the 11-code rendering *(10→11 [CF-6 doc-repair, v3.1])*. |
| **§6.2** Auth / kill-switch | T2.1 menu probe | **RESOLVED — AM-1 + AM-13.** Origin guard on destructive namespace + `MAINTENANCE_ENDPOINTS_ENABLED` kill-switch (default ON, AM-13). `/availability` returns 200 `state:"kill_switched"` on kill-switch OFF — clean hide, never an error. **No FE-side `localStorage` flag.** | T2.1 branches on `state === 'ready'`; T3 canMatch guard mirrors the gate. |
| **§6.3** Menu visibility when auto-cycle disabled | T2.1 menu probe | **RESOLVED — AM-14 (state-enum).** Availability gains `state` enum (`ready \| backend_unsupported \| subsystem_disabled \| kill_switched`); `eligible` is derived (`state === 'ready'`). Gear menu hides on every non-`ready` state. `MAINTENANCE_SERVICE_DISABLED` idea DROPPED (YAGNI). | T2.1 + T3 both gate on `state === 'ready'`. Source-grep pin `state-enum-gating` enforces. |
| **§6.4** Dry-run freshness window | T5.1 stale-dry-run short-circuit | **RESOLVED — 5 min + env, keep.** `MAINTENANCE_DRY_RUN_FRESH_SECONDS=300` default. **AM-16 honest copy caveat:** the window may legitimately expire on slow disks where dry-run itself takes minutes — the operator's prompt to re-run, NOT a defect signal. | T5.1 renders `fresh_until` in the dry-run result panel; honest muted copy "May take several minutes on large databases". |
| **§6.5** `last_run` semantics | T5.1 status card | **RESOLVED, CLOSED — AM-9 (A-6 wins).** `last_run` = latest `succeeded\|failed` of `kind ∈ {auto, manual_execute}` ONLY. `manual_dry_run` never surfaces. The earlier "most-recent-of-any-kind" draft recommendation is overridden. | T5.1 renders single `Last run` card with a small `kind` badge ("auto"/"manual"). `manual_dry_run` history is queryable via `GET /runs/{id}` by ID (out of scope for v1 UI). |
| **§6.6** Concurrency limit | T6.2 poll teardown, T5.1 poll-sub shape | **RESOLVED — single global lane.** Single-flight gate = `MaintenanceRunLock` (in-process) + AM-5 partial unique index DB claim. Both manual classes + auto serialize on ONE global lane. | T5.1 uses 409-adoption (AM-14) to absorb double-click + network-retry classes into "ride along" UX. T6.2 spec asserts adoption. |
| **§6.7** Separate audit channel | (none — no FE change) | **RESOLVED — one channel, AM-15.** `maintenance_runs` IS the audit trail; no separate channel. Forensic fields (`requester_json` {peer_ip, user_agent, origin}) included per AM-15. | No FE surface for audit in v1. `triggered_by` is rendered only in `in_flight` cards (per `CheckpointCleanupInFlight`); never in `last_run` (AM-9 closed). |
| **§6.8** Idempotency key | T1.2 service payload, T5.1 execute flow, T6.2 spec | **RESOLVED, DROPPED — AM-17 (A-1 dropped).** NO `idempotency_key` field; the 409-adoption contract replaces it. On 409 from execute, FE adopts `details.run_id` and resumes polling. **Security framing removed**; the Origin guard (not a key) is the browser defense. | T1.2 has no `crypto.randomUUID` call. T5.1 builds the payload with EXACTLY `{dry_run_run_id, expected_bytes, confirm: true}`. T6.3 source-grep pin `no-idempotency-key` enforces the negative-pin. PR-6 (browser-compat risk for `crypto.randomUUID`) is REMOVED from the risks table. |

---

## Risks specific to Phase 2 (over and above the BE risks in plan-overview §Risks)

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| PR-1 | Availability probe fails silently (e.g. network blip during boot) → operator has no idea why menu is missing | Low | Medium | The probe is idempotent (re-runs on every page load because ngOnInit only runs once per app boot — so a refresh does NOT re-probe). Document in the spec test that the menu reappears after the next page reload (or instruct the operator to hard-reload). Add a T6.2 spec that asserts the menu hides on probe-error AND re-appears on a successful re-probe (the latter requires a way to re-trigger; out of scope for v1). **AM-14 — applies to BOTH the gear-menu probe AND the canMatch route guard.** |
| PR-2 | Poll never terminates (BE never returns a terminal status; e.g. crash mid-run leaves row in 'running' forever) | Medium | Low | `pollRun` has a hard timeout fallback: if the run is still 'running' after 10 minutes (configurable via service constant `POLL_MAX_DURATION_MS`), the FE surfaces an inline error "Run still in progress after 10 min — check daemon logs" and stops polling. The BE should also have a server-side timeout (out of Phase 2 scope; flag in T1 README). **AM-6 — `interrupted` is now a terminal status** (boot-sweep CAS'd), so a daemon restart converges the row to a terminal state in <1s; the 10-min timeout remains the fallback for other stuck-row classes. |
| PR-3 | Stale dry-run UX is confusing — operator clicks execute and gets snack-bar "stale" with no obvious next step | Low | Medium | The snack-bar message is "Re-run the dry-run check before executing" and the dry-run button is highlighted (border-color: `$accent-rose`) when stale. T6.2 spec asserts the message text. **AM-16 honest copy caveat**: the 5-min freshness window may legitimately expire on slow disks where dry-run itself takes minutes — the message is the operator's prompt to re-run, NOT a defect signal. T5.1 muted copy says "May take several minutes on large databases" to set expectations. |
| PR-4 | FE/BE version skew during rollout — operator has the new FE but old BE (or vice versa) | Medium | Medium | T6.2 spec asserts the FE surface tolerates a 503 on `/status` (renders "Maintenance subsystem not initialized — daemon restart required" inline card, not a blank page). The 503 path is already in the FROZEN contract error table. The reverse case (new BE / old FE) is harder: the new BE may add fields the old FE ignores (TS object-literal extra keys are tolerated). **AM-14 specifically warns**: the new FE branches on `state === 'ready'` — an OLD FE that still branches on `eligible === true` will hide the menu on `state: 'kill_switched'` (because `eligible` is derived as `state === 'ready'`, so `kill_switched` returns `eligible: false`). This is graceful but not graceful enough — document in the release notes that the FE + BE MUST ship in the same release for v1. |
| PR-5 | `frontend/dist/` rebuild forgotten — daemon restart ships new BE but old FE → operator sees stale menu (or no menu) | High | Low | The activation steps (§Activation Steps) call this out explicitly with the `ls -la frontend/dist/` mtime check. Document in the merge PR description AND in the operator runbook. This is the project-wide standing trap (see critical-notes: "FE SSE error transcript renderer ... status UNVERIFIED"). **AM-14 amplification**: the FE schema changes (state enum vs boolean eligible, new error codes, new skipped/interrupted fields) compound the skew — if FE is rebuilt but BE is not, the new FE probes `state` and an OLD BE returns the boolean-only shape → FE reads `undefined === 'ready'` → menu hides everywhere. **[R-8, v3 fix pass — order CORRECTED]** activation order is **FE dist build FIRST → daemon rebuild + restart LAST** (the serving daemon must never present a stale dist against the new API); the earlier "daemon rebuild first" wording in this cell was backwards. |
| PR-6 | ~~`crypto.randomUUID()` unavailable in older browsers~~ — **REMOVED** (AM-17 DROPPED `idempotency_key`). No client-side UUID generation exists in the FE any more; the 409-adoption contract replaces the field. The browser-compatibility surface is unchanged. | — | — | — |
| PR-7 | `<dl>` + `<pre>` styling hits the 24kB anyComponentStyle cap when we add error-code branches | Medium | Low | Reserve 8kB for T5.3 SCSS, 4kB for T4.1 (page shell), 4kB for global dialog styling. **AM-14/AM-10 additions**: 3 badge tones (safe/limit/error) for skipped reasons + 2 new banner styles (disabled, error-note) — estimate ~1.5kB additional. Total budget still within 16kB target. Document in commit message. The verify loop (`npm run build`) catches this immediately. |
| PR-8 | Operator double-clicks "Cleanup now" before the dialog opens → two dialogs stack | Low | Low | The execute button is `[disabled]` while `executing() === true`. The dialog open call sets `executing` synchronously in the same tick. T6.2 spec asserts the second click does NOT open a second dialog. **AM-14 — even if the double-click DID slip through**, the BE's single-flight gate (AM-4 + AM-5) returns 409 `run_in_flight` with the in-flight `run_id`; the FE adopts it via the 409-adoption path and rides along instead of producing a second run. The double-click class dissolves to "ride along UX". |
| PR-9 (**AM-14 NEW**) | `canMatch` guard fires a second `/availability` probe on every navigation, doubling load | Low | Low | The probe is cheap (one indexed single-row SELECT, per Focus Area 3 of architect recommendation). The guard runs only on route navigation, not on every change detection. Accept the cost for the stale-dist hardening it buys. Document in commit message. |
| PR-10 (**AM-1 NEW**) | FE accidentally sets a custom `Origin` header or uses `fetch(url, { mode: 'no-cors' })` and the response body becomes unreadable | Medium | Low | T5.1 documents the no-tricks rule; T6.3 source-grep pin `no-fetch-mode-no-cors` (added to the pin table in a follow-up patch if material) — for now, the T6.2 specs assert the FE receives a 403 body and renders the inline banner, which fails LOUDLY if the response body is unreadable. The Playwright cross-origin 403 test (T7.2) covers the browser preflight failure path. |

---

## Acceptance summary (this is what the architect signs off on)

The Phase 2 work is **complete** when ALL of the following hold:

- [ ] T1.1 model types compile and are exported from `frontend/src/app/models/index.ts`. **AM-17:** the `CheckpointCleanupExecuteRequest` type has NO `idempotency_key` field. **AM-14:** `MaintenanceAvailability` carries the `state` enum. **AM-12:** `CheckpointCleanupExecute` carries `advisory` + `expected_duration_ms_hint`. **AM-11:** `CheckpointCleanupBlobsSummary` carries dual-flavor keys. **AM-10:** `CheckpointCleanupDryRun` carries `skipped[]` + `skipped_truncated`. **AM-6:** `CheckpointCleanupRunStatus` includes `'interrupted'`. **AM-13/AM-1/A-8:** `MaintenanceErrorCode` has **11** values *(10→11 [CF-6 doc-repair, v3.1])* , derived from the `MAINTENANCE_ERROR_CODES` as-const tuple.
- [ ] T1.2 service class compiles and exposes 5 typed HTTP methods + 1 poll helper + 1 `adoptRunIdFromError` helper (AM-14, AM-17) + 1 error-mapping helper. `POLL_INTERVAL_MS = 2000` is a `readonly static` constant on the service.
- [ ] T2.1 `checkMaintenanceAvailability()` is wired in `ngOnInit` and the menu appends "Maintenance" only on `state === 'ready'` (AM-14 state-enum gating — NOT the legacy `eligible === true` boolean).
- [ ] T3 lazy route `maintenance/checkpoint-cleanup` is registered before the wildcard with `canMatch: [maintenanceAvailabilityGuard]` (AM-14 route hardening).
- [ ] T4 page shell renders the section registry with a single `Checkpoint Cleanup` section.
- [ ] T5 component renders 4 cards + result panel + error banner + global maintenance_disabled banner; dry-run → confirm → poll → result works end-to-end. **AM-14:** 409-adoption adopts `details.run_id` and resumes polling (no error toast). **AM-10:** skipped[] renders with summary line + reason badge map + `ERROR:*` fallback + `skipped_truncated` notice. **AM-11:** status + result panels branch on `destructive:bool`. **AM-12:** executing card shows `Expected duration: ~X` from the 202 body's `expected_duration_ms_hint`. **AM-6:** interrupted-state result renders the "re-run to converge" affordance card. **AM-16:** honest-duration copy ("May take several minutes on large databases") in dry-run muted text + dialog message.
- [ ] T6.1 service spec, T6.2 component spec, T6.3 source-grep pins all pass under `npx jest src/app/pages/maintenance/`. **AM-16:** spec includes 409-adoption, skipped-render, state-enum gating, 11-code error rendering *(10→11 [CF-6 doc-repair, v3.1])* , interrupted-state render. **AM-17:** `no-idempotency-key` source-grep pin replaces `idempotency-key-present`. **[R-2 + R-addition, v3 fix pass]:** pin table total = **15** (incl. `sections-registry-load-bearing`) *(15 at v3/v3.1; **18 at v3.2** [v3.2, A+C ratified] — +3 projection pins)*.
- [ ] T7 Playwright spec refuses to run against `ensemble_prod` and passes all non-destructive tests on a disposable-PG dev daemon. **AM-16:** spec includes cross-origin 403 case (403 BODY asserted via `context.request.post` [R-12]), kill-switch hide case (`state: 'kill_switched'` → menu hidden), interrupted-state render case, 409-adoption case (no error toast). **[R-2/R-11, v3 fix pass]:** spec total = **14 cases**, run on a dedicated port with `reuseExistingServer: false` + daemon canary.
- [ ] `npx tsc --noEmit -p tsconfig.app.json` passes.
- [ ] `npm run build` (production) passes with NO budget warnings (initial ≤ 1MB warning, anyComponentStyle ≤ 8kB warning).
- [ ] `npm test` passes for the entire FE suite (no regression to existing specs).
- [ ] **All 11 `MaintenanceErrorCode` values are handled in the component (count pin verified)** *(10→11 [CF-6 doc-repair, v3.1])* — `run_in_flight` is silently absorbed by 409-adoption (the count pin still asserts the switch arm exists, even if it doesn't show a banner). `origin_not_trusted` and `maintenance_disabled` were added (AM-1, AM-13).

---

## Contract Feedback (dispositions, post-AM re-freeze)

The frozen API contract in `plan-overview.md` §API Contract is the binding spec for Phase 2. The five open CFs below are **ALL RESOLVED** via the architect's amendments (AM-1…AM-18) and re-recorded in `plan-overview.md` §Contract-Feedback Register addendum. No new contract questions are introduced.

### CF-1. `idempotency_key` field — **DROPPED (AM-17 / A-1)**

**Original concern**: the frozen contract for `POST /execute` listed three body fields; the dispatch instructions called out an idempotency key per overview §6.8; the frozen contract did NOT include it. The FE proposed generating `crypto.randomUUID()` client-side.

**Disposition (AM-17)**: **DROPPED.** Architect ruling: the 409 `run_in_flight` body already carries the in-flight `run_id`; the FE contract is now *on 409 from execute, adopt `details.run_id` and resume polling* — covers double-click and network-retry classes with zero new contract surface. The Origin guard (AM-1), not a key, is the browser defense. Security framing removed.

**What changed**: T1.1 model `CheckpointCleanupExecuteRequest` no longer has the `idempotency_key?: string` field. T5.1 execute flow does NOT generate or send a UUID. T6.3 source-grep pin `idempotency-key-present` REPLACED with `no-idempotency-key` (negative-pin). Risks table PR-6 (browser-compat for `crypto.randomUUID`) REMOVED.

### CF-2. `advisory: "system_busy"` response field — **RATIFIED (AM-12 / A-11)**

**Original concern**: Overview Q1 said "log + `advisory: "system_busy"` in response body if `_is_idle()` is false"; the frozen 202 response body in §4 did NOT include `advisory`.

**Disposition (AM-12)**: **RATIFIED.** 202 body gains `advisory: "system_busy" | null` + `expected_duration_ms_hint` (= the referenced dry-run's `duration_ms`; **unit: ms** — canonical in plan-overview §4 [R-5, v3 fix pass]). The original OQ-draft check #6 typo ("...log + 200 but include...") is fixed: response code is **202**, not 200.

**What changed**: T1.1 model `CheckpointCleanupExecute` adds `advisory` + `expected_duration_ms_hint`. T5.1 stashes `expectedDurationHintMs` signal from the 202 body. T5.2 template renders "Expected duration: ~X" copy in the executing card. T6.3 source-grep pin `expected-duration-hint` enforces the wiring. FE renders an "advisory: system busy" badge ONLY IF the field is set (object-literal extra-key tolerance preserved).

### CF-3. Page shell + service file location — **CONFIRMED (A-9)**

**Original concern**: Phase 2 task list specified `checkpoint-cleanup.service.ts` colocated with the section component at `pages/maintenance/checkpoint-cleanup/`; the original FE Structure section specified `frontend/src/app/services/maintenance.service.ts`. Path conflict.

**Disposition (A-9)**: **CONFIRMED.** Colocated service RATIFIED. The multi-section surface must not share a single-section service file (extensibility requirement). T1.2 + T4.1 already use the colocated path; no change needed.

### CF-4. Polling interval — **CONFIRMED (A-10 / AM-14)**

**Original concern**: Frozen contract did not specify a poll cadence. Overview §Test Strategy → FE said "Polling at 2s on `/runs/{run_id}` is sufficient."

**Disposition (A-10, AM-14)**: **CONFIRMED.** `POLL_INTERVAL_MS = 2000` as a `readonly` `static` constant on `CheckpointCleanupService` + source-grep pin in `checkpoint-cleanup.component.spec.ts`.

**What changed**: T1.2 introduces `static readonly POLL_INTERVAL_MS = 2000 as const`; the `pollRun` default arg uses the constant (so the pin target is the constant name, not a literal `2000`). T6.3 `poll-interval-default-2000` pin updated to assert the constant + the default-arg reference.

### CF-5. `triggered_by` field display in `last_run` — **CLOSED (AM-9)**

**Original concern**: `/status` `in_flight.triggered_by` is a free-form string; `last_run` schema did NOT include `triggered_by`. FE rendering was an open question.

**Disposition (AM-9)**: **CLOSED.** Architect ruling: `last_run` = latest `succeeded|failed` of `kind ∈ {auto, manual_execute}`; `manual_dry_run` never surfaces. The kind badge ("auto"/"manual_execute") is sufficient to disambiguate. **`triggered_by` is NOT extended into `last_run`** — operator UX risk (session-IDs leaking into the UI) outweighs audit visibility in v1. The `triggered_by` field still renders in `in_flight` cards (per `CheckpointCleanupInFlight`).

**What changed**: T1.1 `CheckpointCleanupLastRun` strips `triggered_by` from the union. T5.1 status card renders only the `kind` badge. No further FE work needed.

### CF-6. BE catch-all 500 `internal_error` code — **A-8 SUPERSEDES THE PRE-A-8 COUNT**

**Disposition (A-8)**: **A-8 supersedes the pre-A-8 count.** `MaintenanceErrorCode` union 10→11 including `internal_error`; the FE surfaces the literal verbatim (`toErrorBody` coercion to `not_initialized` removed; unknown-code/absent-body fallback retained). The 15 source-grep pin total is a separate count and is unchanged. Flagged for architect ratification at merge. *(v3.1 [CF-6 doc-repair]: the doc lag this disposition flagged is closed — frozen table row 11 in plan-overview, union/pin/mapping sweep to 11 in this file, BE case 67 in phase1; ready for the one-line ratification.)*

> **Architect ratified 2026-09-27: CF-6 union=11 (A-8) — v3.1 doc-repair verified (4/4 steps; zero 10-code/66 residues; counts 67 BE / 11 FE / 15 pins / 14 Playwright agree at every site); FE tuple mirror verified SHIPPED-EXACT against the BE wire table — `MAINTENANCE_ERROR_CODES` = 11 entries in `frontend/src/app/models/index.ts` (union derived via `(typeof …)[number]`, no hand-written duplicate), `isKnownErrorCode` single-sourced and consumed at `checkpoint-cleanup.service.ts:322`, router catch-all live at `daemon/routers/maintenance.py:382`. D4 re-based: the artifact landed on-branch after the original worktree read — no drift remains.** *(Counts in this stamp are the v3.1-time values — superseded at v3.2: 72 BE / 18 pins; union 11 and Playwright 14 unchanged [v3.2, A+C ratified].)*

---

## Sign-off Checklist

- [ ] Architect reviewed and ratified all 18 amendments AM-1…AM-18 in `architecture-recommendation.md` (READY-WITH-AMENDMENTS verdict). The re-freeze marker on `plan-overview.md` now reads **"API Contract v3 — frozen 2026-09-27"** (v3 = reviewer fix pass over the architect-stamped v2; architect's quick delta pass pending — plan-overview Re-freeze Checklist rows 5–6).
- [ ] Phase 1 lead confirms: contract v3 signed, all 9 tasks have acceptance criteria (including AM-16 new BE cases a–d + Origin guard matrix + boot sweep + dual-arm conflict).
- [ ] Phase 2 lead confirms: this phase-2 detail plan conforms to the re-frozen contract v3 — schema re-sync complete (T1.1), state-enum gating (T2 + T3), 409-adoption (T1.2 + T5.1), dual-flavor branching (T5.1 + T5.2), skipped[] render with reason badge map (T5.1 + T5.2), interrupted-state affordance (T5.1 + T5.2), expected_duration_ms_hint display (T5.1 + T5.2), `no-idempotency_key` negative-pin (T6.3), honest duration copy (T5.1 muted + dialog message). No new design introduced.
- [ ] **CF-1 through CF-5 dispositions applied** (AM-17 DROPPED, AM-12 RATIFIED, A-9 CONFIRMED, A-10 CONFIRMED, AM-9 CLOSED). The Contract Feedback section now records the architect's resolutions; no open CFs remain.
- [ ] **§6.1 through §6.8 dispositions applied** (A-8 structured dict, AM-1/AM-13 origin+kill-switch, AM-14 state-enum, 5 min window + env, AM-9 last_run scope, single global lane, AM-15 one audit channel, AM-17 dropped idempotency). The Open Questions table now records the architect's resolutions; no open OQs remain.
- [ ] Test lead confirms: disposable-PG harness ready, Jest specs cover the 11-code error union *(10→11 [CF-6 doc-repair, v3.1])* + 409-adoption + skipped-render + state-enum gating + interrupted-state render. Playwright spec drafted with AM-16 cross-origin 403 + kill-switch hide + 409-adoption + interrupted-state cases.
- [ ] Ops lead confirms: activation runbook drafted (Phase 3), kill-switch default `MAINTENANCE_ENDPOINTS_ENABLED=1` documented (AM-13 hide-not-error semantics), FE dist rebuild trap warning included in §Activation Steps.
- [ ] T1 lead confirms all model + service types compile and the service spec pins the 5 endpoints + poll helper + `adoptRunIdFromError` + 11-code error mapping *(10→11 [CF-6 doc-repair, v3.1])*.
- [ ] T2/T3 lead confirms gear-menu probe branches on `state === 'ready'` (AM-14) AND the route is gated by `canMatch: [maintenanceAvailabilityGuard]` (AM-14 route hardening).
- [ ] T4 lead confirms page shell + section registry + 2-3 acceptance tests for the registry pattern.
- [ ] T5 lead confirms component compiles, renders, and passes `npm run build` with no budget warnings. **Skipped-render block** (AM-10), **dual-flavor branching** (AM-11), **interrupted-state card** (AM-6), **expected-duration-hint copy** (AM-12), and **409-adoption logic** (AM-14) all visible in the rendered template.
- [ ] T6 lead confirms all 11 error codes are handled (count pin: 11 — was 10 at v3, 8 in the v1 draft; 10→11 residue fixed during the v3.2 count sweep [v3.2, A+C ratified]), the pin table totals **18** *(15 at v3/v3.1 [R-2 + sections-registry addition, v3 fix pass]; +3 projection pins at v3.2)*, source-grep pins include `no-idempotency-key` (AM-17), `state-enum-gating` (AM-14), `409-adoption-wired` (AM-14/AM-17), `skipped-render-wired` (AM-10), `interrupted-affordance` (AM-6), `dual-flavor-branch` (AM-11), `expected-duration-hint` (AM-12), `fresh_until-shown` (AM-16), `sections-registry-load-bearing` ([R-addition]). Full `npm test` is green.
- [ ] T7 lead confirms Playwright spec refuses to run against prod, includes cross-origin 403 (**403 body asserted** [R-12]) + kill-switch hide + 409-adoption (no error toast) + interrupted-state render + dual-flavor branch coverage + skipped badge + state-enum gating, runs on a **dedicated port with `reuseExistingServer: false` + daemon canary** [R-11], totals **14 cases** [R-2], and passes on disposable-PG dev daemon.

---

## v3.2 FE Work Block (dry-run projection — A+C) [v3.2, A+C ratified]

**Spec (normative): `dry-run-projection-amendment.md`** — §CONTRACT v3.2 AMENDMENT TEXT, §FE display contract, §Test deltas (FE). Branch: `feature/dry-run-projection-v3.2` @ `b8c8a28a`. Additive-only; error-code union stays 11; Playwright stays 14; pins 15 → **18**.

### Models additions (T1.1)

- `CheckpointCleanupDryRun` gains three additive fields (default 0 when absent — v3.1 clients unaffected): `bytes_reclaimable_now: number`, `bytes_reclaimable_after_row_prune: number`, `bytes_reclaimable_total: number`.
- New echo-block type: `CheckpointCleanupProjectionEcho = { bytes_reclaimable_now_at_dry_run: number; bytes_reclaimable_after_row_prune_at_dry_run: number }`; `CheckpointCleanupSummary` gains optional `projection?: CheckpointCleanupProjectionEcho` (present on `manual_execute` rows; absent on auto rows — R-5).

### Display contract (normative copy per amendment §FE display contract)

1. **Three-number card** (dry-run card): "This run: ~{now} · After this run (run cleanup again): ~{after} · Combined: ~{total}". Zero components render "—" (never "0 bytes" — a zero component is absence, not a promise). Never-pruned sub-copy: *"On a DB that has never run retention, run 1 deletes rows only; run 2 frees the blob bytes."*
2. **Skipped-flag banner** (R-4): when `skipped.length > 0`, render "N pairs skipped — cleanup effectiveness may be understated" near the projection.
3. **Journey-copy confirm dialog** (per-run consent + journey context; supersedes the v3 message template at implementation time): *"This run will permanently delete ~{fmt(now)} of unreferenced blobs and {rows} excess checkpoint rows. After this run, ~{fmt(after)} more becomes reclaimable by running cleanup again. This may take several minutes on large databases. This cannot be undone."*
4. **Post-run convergence banner**: when execute succeeded AND `projection.bytes_reclaimable_after_row_prune_at_dry_run > 0` → "Run cleanup again to reclaim ~{fmt(…)} more". The CTA starts a **NEW dry-run** (never a silent execute — the banner is an estimation affordance, execute stays consent-gated); disabled while a run is in flight; hides once a fresh dry-run reports `now == 0` (converged). Survives page refresh via the run-row `projection` echo block.

### New source-grep pins (15 → 18; specs per amendment §Test deltas FE)

| Pin | What it pins | Failure mode it catches |
|---|---|---|
| `projection-fields-render` (**v3.2 NEW**) | Template renders the three-number card from `bytes_reclaimable_now/after_row_prune/total` AND renders "—" for zero components AND the skipped-flag banner when `skipped.length > 0` | Projection drops on the floor (operators see only `would_free_bytes` — the incident UX regresses) or a zero renders as a promise |
| `confirm-message-journey-copy` (**v3.2 NEW**) | Confirm-dialog `data.message` contains `fmt(now)`, the rows count, `fmt(after)`, AND the "running cleanup again" anchor (extends — does not replace — the existing `byte-echo-in-confirm-message` pin) | Dialog reverts to per-run-only copy; the journey duty falls back on one number (the v3 defect) |
| `run-again-banner-when-projection-nonzero` (**v3.2 NEW**) | Component renders the convergence banner iff `projection.bytes_reclaimable_after_row_prune_at_dry_run > 0`, the CTA invokes the DRY-RUN flow (never `execute` directly), the banner is disabled while in-flight, and it hides on fresh `now == 0` | Silent re-execute path appears (consent bypass) or the banner nags after convergence |

**Pin table total: 18** (15 at v3/v3.1 + 3). Unchanged: error-code union (11), Playwright (14), all v3.1 pins.
