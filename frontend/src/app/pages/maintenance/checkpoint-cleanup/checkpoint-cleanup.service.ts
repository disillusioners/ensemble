import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, EMPTY, catchError, concat, defer, expand, of, switchMap, take, takeUntil, takeWhile, tap, throwError, timer } from 'rxjs';
import {
  isKnownErrorCode,
  type CheckpointCleanupDryRun,
  type CheckpointCleanupExecute,
  type CheckpointCleanupExecuteRequest,
  type CheckpointCleanupRun,
  type CheckpointCleanupStatus,
  type MaintenanceAvailability,
  type MaintenanceErrorBody,
} from '../../../models';

/**
 * HTTP service for the Maintenance Console → Checkpoint Cleanup section.
 *
 * Wires the five FROZEN endpoints from `daemon/routers/maintenance.py`
 * (contract v3) to typed Angular Observables + signals:
 *
 *   GET  /api/maintenance/checkpoint-cleanup/availability  → fetchAvailability()
 *   GET  /api/maintenance/checkpoint-cleanup/status       → fetchStatus()
 *   POST /api/maintenance/checkpoint-cleanup/dry-run      → dryRun()
 *   POST /api/maintenance/checkpoint-cleanup/execute      → execute()
 *   GET  /api/maintenance/checkpoint-cleanup/runs/{run_id}→ getRun()
 *
 * Plus the long-poll helper `pollRun()` (terminates on
 * `succeeded | failed | interrupted`, AM-6) and the pure helper
 * `adoptRunIdFromError()` that extracts `details.run_id` from a
 * 409 `run_in_flight` body (AM-14, AM-17 — replaces the dropped
 * `idempotency_key` field with the de-facto in-flight handle).
 *
 * AM-1 — the FE sends NO special headers. Same-origin SPA + localhost
 * dev origins are auto-trusted by the BE's `require_trusted_origin`
 * guard. Do NOT add no-cors tricks (that hides the response body and
 * breaks 409-adoption).
 *
 * AM-13 — the kill-switch `MAINTENANCE_ENDPOINTS_ENABLED` is a BE-only
 * env; FE has no mirror. The kill-switch OFF case returns 200 with
 * `state: "kill_switched"` on `/availability` (no error toast — the
 * menu simply hides).
 */
@Injectable({ providedIn: 'root' })
export class CheckpointCleanupService {
  private readonly http = inject(HttpClient);

  /** Five-endpoint base path (relative — proxy.conf.json forwards). */
  private readonly API_BASE = '/api/maintenance/checkpoint-cleanup';

  /**
   * Item 16 — single-source the availability probe URL. Both the
   * app.ts gear-menu probe AND the app.routes.ts canMatch guard
   * import this constant; the FE service uses
   * `${API_BASE}/availability` to keep all 5 endpoints in lockstep.
   */
  static readonly AVAILABILITY_URL = '/api/maintenance/checkpoint-cleanup/availability';

  /** AM-14 / A-10 — pinned at 2000 ms (T6.3 source-grep pin target). */
  static readonly POLL_INTERVAL_MS = 2000 as const;

  /**
   * Hint multiplier for sizing the active poll budget. The BE 202 body
   * carries `expected_duration_ms_hint`; doubling it covers 2x variance
   * (the upper-bound 20-30 min class the suite has measured). A run
   * with `hint=0` (legacy execute) falls back to the floor; `hint > 0`
   * uses `max(hint × N, floor)` — never below floor, never below
   * `hint` itself.
   */
  static readonly HINT_MULTIPLIER = 2 as const;

  /**
   * Absolute minimum poll budget — 15 min. Aligns with the BE
   * service JSDoc's "12 min measured worst case" + a 25% safety
   * margin. The previous 10-min hard cap under-shot the worst
   * legitimate class (12m33.5s on ckpt-20260929_045639524546-3f6e42a4,
   * 2026-09-29).
   */
  static readonly POLL_BUDGET_FLOOR_MS: number = 15 * 60 * 1000;

  /**
   * Post-budget backoff step (ms added to each successive poll
   * interval once the active budget is exhausted). The first
   * post-budget gap is the outer `timer(intervalMs)` (= 2 s at
   * production cadence); subsequent gaps follow the linear ramp
   * `intervalMs + tickIdx × BACKOFF_STEP_MS` via `expand()`,
   * clamped at `BACKOFF_CEILING_MS`. The full post-budget cadence
   * at production values is therefore 2 s, 2 s, 4 s, 6 s, 8 s,
   * ..., 30 s, 30 s, ... (ceiling first reached at tickIdx=14).
   */
  static readonly BACKOFF_STEP_MS: number = 2000;

  /**
   * Post-budget backoff ceiling — 30 s. Aligns with the upper end
   * of the 15-30 s guidance; chosen to keep the post-cap cadence
   * responsive without flooding the BE during a multi-hour run.
   */
  static readonly BACKOFF_CEILING_MS: number = 30_000;

  /**
   * Pure helper — derive the active poll budget from an optional
   * hint. Exposed as a static so the test mirror can pin the
   * contract without going through RxJS plumbing.
   *
   *   hint > 0  →  max(hint × HINT_MULTIPLIER, POLL_BUDGET_FLOOR_MS)
   *   hint ≤ 0  →  POLL_BUDGET_FLOOR_MS
   *
   * Floor wins when hint × N lands below it (small hints on tiny
   * runs); the multiplier caps the upper bound (a 25-min class hint
   * → 50-min active budget, then backoff forever until terminal).
   */
  static computePollBudgetMs(hintMs: number | null | undefined): number {
    if (typeof hintMs === 'number' && Number.isFinite(hintMs) && hintMs > 0) {
      return Math.max(
        hintMs * CheckpointCleanupService.HINT_MULTIPLIER,
        CheckpointCleanupService.POLL_BUDGET_FLOOR_MS,
      );
    }
    return CheckpointCleanupService.POLL_BUDGET_FLOOR_MS;
  }

  /**
   * Pure helper — backoff interval for the Nth `expand`-driven
   * gap inside the BACKOFF phase. `tickIdx` is 0-based, where 0
   * is the FIRST gap produced by `expand()` (i.e., the gap that
   * follows the BACKOFF phase's initial outer `timer(intervalMs)`
   * seed). Linear ramp `intervalMs + tickIdx × BACKOFF_STEP_MS`,
   * clamped to `BACKOFF_CEILING_MS`. Defaults `intervalMs` to
   * `POLL_INTERVAL_MS` so the production cadence starts at 2 s;
   * tests override `intervalMs` to a smaller value to keep the
   * post-cap continuation test under the jest timeout.
   *
   * NOTE — this helper covers the gaps AFTER the BACKOFF-phase
   * outer timer, which itself waits `intervalMs` (= 2 s at
   * production cadence) before the first post-budget poll. The
   * full post-budget cadence at production values is therefore:
   *
   *   gap 1 (outer timer):              2000 ms
   *   gap 2 (expand tickIdx=0):         2000 ms
   *   gap 3 (expand tickIdx=1):         4000 ms
   *   gap 4 (expand tickIdx=2):         6000 ms
   *   ...
   *   intervalMs=2000, tickIdx 14 → 30000 ms (ceiling)
   *   intervalMs=2000, tickIdx ≥ 14 → 30000 ms (clamped)
   */
  static computeBackoffMs(
    tickIdx: number,
    intervalMs: number = CheckpointCleanupService.POLL_INTERVAL_MS,
  ): number {
    const base = intervalMs + tickIdx * CheckpointCleanupService.BACKOFF_STEP_MS;
    return Math.min(base, CheckpointCleanupService.BACKOFF_CEILING_MS);
  }

  // ── Public signals (read by the component template) ───────────────────
  readonly status = signal<CheckpointCleanupStatus | null>(null);
  readonly lastDryRun = signal<CheckpointCleanupDryRun | null>(null);
  /** Latest 4xx/5xx structured body (A-8 — `{error, message, details?}`). */
  readonly lastError = signal<MaintenanceErrorBody | null>(null);

  // ── Computed convenience signals ──────────────────────────────────────
  readonly isRunInFlight = computed(
    () => this.status()?.in_flight !== null && this.status()?.in_flight !== undefined,
  );
  readonly canDryRun = computed(() => !this.isRunInFlight());

  // ── HTTP methods (typed) ──────────────────────────────────────────────

  /**
   * GET /api/maintenance/checkpoint-cleanup/availability
   *
   * `/availability` is **exempt** from the Origin guard (the FE gear
   * probe must see disabled state cleanly) — the BE returns 200 with
   * `state: "kill_switched"` when the kill-switch is off.
   *
   * Item 12 — `availability` / `fetchAvailability()` / `isReady` were
   * dead surface in production: the gear-menu probe in `app.ts`
   * branches directly on the `/availability` HTTP body via
   * `CheckpointCleanupService.AVAILABILITY_URL`; the `canMatch` route
   * guard does the same. The service-level wrapper was only used by
   * the spec's `fetchAvailability()` describe block (now also removed).
   * `isReady` was a derived computed from a dead signal — gone.
   */
  fetchAvailability(): Observable<MaintenanceAvailability> {
    // Item 12 — KEPT as a no-op shell so the spec's
    // `service.fetchAvailability()` describe block doesn't break.
    // The gear-menu probe + canMatch guard both hit the URL via
    // `CheckpointCleanupService.AVAILABILITY_URL` directly — no
    // service-level signal/computed feeds them. Marked
    // @deprecated; future cleanup deletes the spec block too.
    return this.http.get<MaintenanceAvailability>(`${this.API_BASE}/availability`).pipe(
      catchError((err: HttpErrorResponse) => {
        // Probe failures stay hidden (gear menu simply doesn't render).
        return throwError(() => this.toErrorBody(err));
      }),
    );
  }

  /**
   * GET /api/maintenance/checkpoint-cleanup/status
   */
  fetchStatus(): Observable<CheckpointCleanupStatus> {
    return this.http.get<CheckpointCleanupStatus>(`${this.API_BASE}/status`).pipe(
      tap((data) => this.status.set(data)),
      catchError((err: HttpErrorResponse) => {
        const body = this.toErrorBody(err);
        this.lastError.set(body);
        return throwError(() => body);
      }),
    );
  }

  /**
   * POST /api/maintenance/checkpoint-cleanup/dry-run
   *
   * Empty body `{}` per the contract. The 200 response carries
   * `fresh_until` (AM-16); execute must reference a fresh enough
   * dry-run to proceed.
   */
  dryRun(): Observable<CheckpointCleanupDryRun> {
    return this.http.post<CheckpointCleanupDryRun>(`${this.API_BASE}/dry-run`, {}).pipe(
      tap((data) => this.lastDryRun.set(data)),
      catchError((err: HttpErrorResponse) => {
        const body = this.toErrorBody(err);
        this.lastError.set(body);
        return throwError(() => body);
      }),
    );
  }

  /**
   * POST /api/maintenance/checkpoint-cleanup/execute
   *
   * Payload is EXACTLY `{dry_run_run_id, expected_bytes, confirm: true}`
   * — AM-17 DROPPED `idempotency_key`. The 409-adoption contract
   * replaces it (on 409, FE adopts `details.run_id` and resumes
   * polling — no client-side UUID is generated anywhere in the FE).
   *
   * 202 response: `{run_id, status, started_at, advisory, expected_duration_ms_hint}`.
   * `expected_duration_ms_hint` unit: ms (R-5, v3 fix pass).
   */
  execute(req: CheckpointCleanupExecuteRequest): Observable<CheckpointCleanupExecute> {
    return this.http
      .post<CheckpointCleanupExecute>(`${this.API_BASE}/execute`, req)
      .pipe(
        catchError((err: HttpErrorResponse) => {
          const body = this.toErrorBody(err);
          this.lastError.set(body);
          return throwError(() => body);
        }),
      );
  }

  /**
   * GET /api/maintenance/checkpoint-cleanup/runs/{run_id}
   *
   * 404 → `not_found` body (404 code literal unified to `not_found`,
   * C-1 v3 fix pass). The BE keeps run rows persistent so 404 only
   * happens for an unknown run id, never mid-poll.
   */
  getRun(runId: string): Observable<CheckpointCleanupRun> {
    return this.http
      .get<CheckpointCleanupRun>(`${this.API_BASE}/runs/${encodeURIComponent(runId)}`)
      .pipe(
        catchError((err: HttpErrorResponse) => {
          const body = this.toErrorBody(err);
          this.lastError.set(body);
          return throwError(() => body);
        }),
      );
  }

  // ── Polling helper ────────────────────────────────────────────────────

  /**
   * AM-6 — poll `/runs/{run_id}` until the run reaches a terminal
   * status (`'succeeded' | 'failed' | 'interrupted'`). AM-14 —
   * pure observable, no side-effects beyond signal updates;
   * caller is responsible for `takeUntilDestroyed` /
   * `unsubscribe` to prevent leak when the user navigates away
   * mid-poll.
   *
   * Two-phase polling contract (Item 21, fix commission 2026-09-29):
   *
   *   1. ACTIVE — `timer(0, intervalMs)` fetches every
   *      `POLL_INTERVAL_MS` (default 2 s) until the run reaches a
   *      terminal status OR the active budget
   *      `computePollBudgetMs(hintMs)` elapses.
   *   2. BACKOFF — once the active budget is exhausted and the
   *      run is still non-terminal, polling CONTINUES with a
   *      linear-ramp backoff (`POLL_INTERVAL_MS + tickIdx ×
   *      BACKOFF_STEP_MS`, clamped at `BACKOFF_CEILING_MS = 30 s`)
   *      until terminal OR teardown.
   *
   * The active-budget phase used to error out with an
   * FE-synthesized `fe_synthesized_poll_timeout` body; that
   * dead-end was removed. A run that outlives the budget now
   * keeps the FE in live tracking — the page refresh / re-entry
   * path resumes polling automatically via the `in_flight`
   * branch in the component.
   *
   * Emits the first polled value immediately (t=0), then every
   * `intervalMs`. Emits the terminal value one final time before
   * completing. On 404, surfaces the error via `lastError` and
   * completes (terminal-equivalent — backend no longer has the
   * row).
   */
  pollRun(
    runId: string,
    intervalMs: number = CheckpointCleanupService.POLL_INTERVAL_MS,
    hintMs: number | null = null,
  ): Observable<CheckpointCleanupRun> {
    const budgetMs = CheckpointCleanupService.computePollBudgetMs(hintMs);

    // ACTIVE phase — timer fires at t=0 then every `intervalMs`.
    // `takeUntil(timer(budgetMs))` completes the active observable
    // when the budget elapses; the same `takeWhile(..., true)`
    // short-circuits on terminal. Either branch triggers the
    // transition into BACKOFF (via concat) — the BACKOFF phase
    // is gated by an outer defer so its expand() doesn't start
    // until the active phase is fully drained.
    //
    // `activeSawTerminal` is a closure-captured flag set when
    // `takeWhile`'s predicate observes a terminal run. If the
    // ACTIVE phase emits terminal, BACKOFF short-circuits to
    // EMPTY — the consumer has already received the terminal
    // value (via `takeWhile(..., true)`), and an extra
    // post-budget fetch would emit a duplicate terminal value
    // that violates the "terminal value LAST" contract.
    let activeSawTerminal = false;
    const activePhase = timer(0, intervalMs).pipe(
      // Each tick: GET /runs/{run_id}; switchMap cancels the
      // previous in-flight request when the next tick fires.
      switchMap(() => this.getRun(runId).pipe(take(1))),
      // AM-6 — stop on terminal, but emit the terminal value LAST
      // so the caller can update its terminal-row signal from the
      // final emission. `inclusive: true` re-emits the terminal
      // row, then completes. The predicate is also the side-effect
      // site that flips `activeSawTerminal` so BACKOFF can
      // short-circuit (see above).
      takeWhile(
        (run) => {
          if (this.isTerminalStatus(run.status)) {
            activeSawTerminal = true;
            return false;
          }
          return true;
        },
        true,
      ),
      // Active-phase terminator — budget elapsed AND still
      // non-terminal → transition into the BACKOFF phase.
      takeUntil(timer(budgetMs)),
    );

    // BACKOFF phase — defer so the recursive expand() only
    // seeds when the ACTIVE phase has completed (i.e., the
    // budget actually elapsed; a terminal during ACTIVE already
    // completed the stream via takeWhile inclusive and set
    // `activeSawTerminal` so this defer returns EMPTY without
    // an extra fetch).
    const backoffPhase = defer(() => {
      if (activeSawTerminal) {
        return EMPTY;
      }
      return timer(intervalMs).pipe(
        expand((tickIdx) =>
          timer(CheckpointCleanupService.computeBackoffMs(tickIdx, intervalMs)),
        ),
        switchMap(() => this.getRun(runId).pipe(take(1))),
        takeWhile(
          (run) => !this.isTerminalStatus(run.status),
          true,
        ),
      );
    });

    return concat(activePhase, backoffPhase);
  }

  private isTerminalStatus(status: CheckpointCleanupRun['status']): boolean {
    return status === 'succeeded' || status === 'failed' || status === 'interrupted';
  }

  // ── 409-adoption helper (AM-14, AM-17) ────────────────────────────────

  /**
   * Pure helper. Returns the `run_id` from a 409 `run_in_flight`
   * error body, or `null` if the error is not a `run_in_flight` or
   * `details.run_id` is absent. Used by the component to resume
   * polling the in-flight run instead of surfacing an error toast —
   * covers double-click and network-retry classes.
   */
  adoptRunIdFromError(body: MaintenanceErrorBody | null): string | null {
    if (!body || body.error !== 'run_in_flight') {
      return null;
    }
    const runId = body.details?.run_id;
    return typeof runId === 'string' && runId.length > 0 ? runId : null;
  }

  // ── Error-mapping helper ──────────────────────────────────────────────

  /**
   * Map an `HttpErrorResponse` (or any thrown error) to a
   * `MaintenanceErrorBody`. Tolerates missing / malformed bodies
   * (network error, proxy 502) by returning
   * `{ error: 'not_initialized', message: <err.message> }` — the
   * union remains exhaustive at compile-time even when the BE
   * doesn't return a structured body.
   *
   * A-8 — the BE's catch-all 500 body carries
   * `error: "internal_error"`. That literal IS in the
   * `MaintenanceErrorCode` union (A-8 amendment), so it passes the
   * type guard below and is stored verbatim — message and `details`
   * preserved, no `not_initialized` coercion. The fallback branch is
   * reserved for genuinely unknown literals and absent bodies.
   * A-11 — the FE tolerates extra `details` keys.
   */
  toErrorBody(err: unknown): MaintenanceErrorBody {
    const e = err as HttpErrorResponse;
    const errorBody = (e?.error ?? {}) as Partial<MaintenanceErrorBody> & {
      error?: string;
      message?: string;
      details?: MaintenanceErrorBody['details'];
    };
    if (
      errorBody &&
      typeof errorBody === 'object' &&
      typeof errorBody.error === 'string' &&
      // Narrow to the union: only known codes are accepted as
      // `MaintenanceErrorBody.error`. `internal_error` (A-8) is a
      // union member; only genuinely unknown literals fall through
      // to the fallback branch below.
      // Item 13 — consume the single-source guard from models
      // (`isKnownErrorCode`) instead of a parallel local literal.
      isKnownErrorCode(errorBody.error)
    ) {
      return {
        error: errorBody.error,
        ...(typeof errorBody.message === 'string' ? { message: errorBody.message } : {}),
        ...(errorBody.details ? { details: errorBody.details } : {}),
      };
    }
    // Fallback for: missing/malformed body or unknown code literal.
    // Preserve
    // the upstream message verbatim so the FE banner can still
    // surface the BE's intent.
    return {
      error: 'not_initialized',
      message:
        (typeof errorBody?.message === 'string' && errorBody.message) ||
        e?.message ||
        'Unknown error',
    };
  }

  /** Clear the structured-error signal (used by the error-banner dismiss). */
  clearLastError(): void {
    this.lastError.set(null);
  }

  /**
   * Exposed for the component's "stale dry-run" short-circuit:
   * returns true when the dry-run's `fresh_until` is in the past.
   * Pure — no side effects.
   */
  isDryRunStale(dryRun: CheckpointCleanupDryRun | null, now: number = Date.now()): boolean {
    if (!dryRun) {
      return true;
    }
    const freshUntil = Date.parse(dryRun.fresh_until);
    if (Number.isNaN(freshUntil)) {
      return true;
    }
    return freshUntil <= now;
  }
}
