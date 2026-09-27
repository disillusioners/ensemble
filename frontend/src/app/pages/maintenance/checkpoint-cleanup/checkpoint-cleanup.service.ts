import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, take, takeWhile, tap, throwError, timer } from 'rxjs';
import { switchMap } from 'rxjs/operators';
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
   * AM-6 / PR-2 — hard-timeout fallback. If a polled run stays in
   * `'running'` past this window, the service surfaces an inline
   * "still in progress" error and stops polling. The BE boot-sweep
   * converges orphaned rows in <1s after a daemon restart; this is
   * the fallback for other stuck-row classes. Pin target: 10 minutes
   * (T6.1 fake-timers test verifies the timeout fires).
   */
  static readonly POLL_MAX_DURATION_MS: number = 10 * 60 * 1000;

  // ── Public signals (read by the component template) ───────────────────
  readonly availability = signal<MaintenanceAvailability | null>(null);
  readonly status = signal<CheckpointCleanupStatus | null>(null);
  readonly lastDryRun = signal<CheckpointCleanupDryRun | null>(null);
  /** Latest 4xx/5xx structured body (A-8 — `{error, message, details?}`). */
  readonly lastError = signal<MaintenanceErrorBody | null>(null);
  /** Last completed polled run (set when `pollRun()` terminates). */
  readonly lastRun = signal<CheckpointCleanupRun | null>(null);

  // ── Computed convenience signals ──────────────────────────────────────
  readonly isRunInFlight = computed(
    () => this.status()?.in_flight !== null && this.status()?.in_flight !== undefined,
  );
  readonly canDryRun = computed(() => !this.isRunInFlight());

  /**
   * AM-14 — derived `eligible` from the state enum. UI hides the menu
   * on every non-`ready` state.
   */
  readonly isReady = computed(() => this.availability()?.state === 'ready');

  // ── HTTP methods (typed) ──────────────────────────────────────────────

  /**
   * GET /api/maintenance/checkpoint-cleanup/availability
   *
   * `/availability` is **exempt** from the Origin guard (the FE gear
   * probe must see disabled state cleanly) — the BE returns 200 with
   * `state: "kill_switched"` when the kill-switch is off.
   */
  fetchAvailability(): Observable<MaintenanceAvailability> {
    return this.http.get<MaintenanceAvailability>(`${this.API_BASE}/availability`).pipe(
      tap((data) => this.availability.set(data)),
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
   * status (`'succeeded' | 'failed' | 'interrupted'`), or until the
   * hard-timeout window elapses. AM-14 — pure observable, no
   * side-effects beyond signal updates; caller is responsible for
   * `takeUntilDestroyed` / `unsubscribe` to prevent leak when the
   * user navigates away mid-poll.
   *
   * Emits the first polled value immediately, then every
   * `intervalMs` (default `POLL_INTERVAL_MS = 2000`). Emits the
   * terminal value one final time before completing. On 404,
   * surfaces the error via `lastError` and completes. On
   * hard-timeout, surfaces an inline "still in progress" error and
   * completes.
   */
  pollRun(
    runId: string,
    intervalMs: number = CheckpointCleanupService.POLL_INTERVAL_MS,
  ): Observable<CheckpointCleanupRun> {
    const start = Date.now();
    const maxMs = CheckpointCleanupService.POLL_MAX_DURATION_MS;
    return timer(0, intervalMs).pipe(
      // Each tick: GET /runs/{run_id}; switchMap cancels the previous
      // in-flight request when the next tick fires (matters at the
      // timeout boundary).
      switchMap(() => {
        if (Date.now() - start >= maxMs) {
          // PR-2 / Item 4 — the FE poll budget elapsed before the run
          // reached a terminal status. The wire body carries
          // `error: 'not_initialized'` (closest wire-compatible literal;
          // it is a union member and remains the wire-facing truth) +
          // `details.fe_synthesized_poll_timeout: true` so the FE
          // display layer can branch into the FE-only `'poll_stale'`
          // sentinel (see `MaintenanceDisplayCode`). The literal
          // `'poll_stale'` is NEVER sent — it is a display-only
          // sentinel OUTSIDE the BE-mirrored `MaintenanceErrorCode`
          // union.
          const stuck: MaintenanceErrorBody = {
            error: 'not_initialized',
            message:
              `Run ${runId} is still in progress after ` +
              `${Math.round(maxMs / 60000)} min — check daemon logs.`,
            details: { fe_synthesized_poll_timeout: true },
          };
          this.lastError.set(stuck);
          return throwError(() => stuck);
        }
        return this.getRun(runId).pipe(
          take(1),
          catchError((err) => throwError(() => err)),
        );
      }),
      tap((run) => {
        this.lastRun.set(run);
      }),
      // AM-6 — stop on terminal, but emit the terminal value LAST so
      // the caller can update its `lastRun` signal from the final
      // emission. `inclusive: true` re-emits the terminal row, then
      // completes.
      takeWhile(
        (run) => !this.isTerminalStatus(run.status),
        true,
      ),
    );
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
  toErrorBody(err: HttpErrorResponse | unknown): MaintenanceErrorBody {
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
