// CheckpointCleanupService — logic-mirror spec (no TestBed).
//
// Mirrors `frontend/src/app/services/instance.service.spec.ts:14-35`
// — a hand-rolled mock HttpClient + a parallel `TestableCheckpointCleanupService`
// that copies the production surface verbatim. This is the house style:
// avoid Angular DI in tests by constructing the testable mirror
// directly with the mock.
//
// The 5 HTTP methods map to the right path + verb; `pollRun` terminates
// on each terminal status (incl. `interrupted`); `adoptRunIdFromError`
// returns the `details.run_id` on a 409 `run_in_flight` body and
// `null` otherwise. AM-17 negative pin: the execute payload MUST be
// EXACTLY `{dry_run_run_id, expected_bytes, confirm: true}` — no
// `idempotency_key`, no client-side UUID.

import { signal } from '@angular/core';
import { Observable, of, throwError } from 'rxjs';
import { catchError, switchMap, take, takeWhile, tap, throwError as throwErr, timer } from 'rxjs';
import {
  isKnownErrorCode,
  type CheckpointCleanupBlobsSummary,
  type CheckpointCleanupDryRun,
  type CheckpointCleanupExecute,
  type CheckpointCleanupExecuteRequest,
  type CheckpointCleanupRun,
  type CheckpointCleanupStatus,
  type MaintenanceAvailability,
  type MaintenanceErrorBody,
} from '../../../models';
import {
  AVAILABILITY_READY,
  BLOBS_DRY,
  DRY_RUN,
  EXECUTE_RESP,
  STATUS,
  makeRun,
} from './__fixtures__/fixtures';

// Note: `MaintenanceDisplayCode` lives in models/index.ts but is a
// FE-only display sentinel — the service mirror does NOT consume it.
// The wire `error` field stays `MaintenanceErrorCode` (11 members).

// ── Mock HttpClient ──────────────────────────────────────────────────────

interface RecordedCall {
  method: 'GET' | 'POST';
  url: string;
  body: unknown;
}

class MockHttpClient {
  readonly calls: RecordedCall[] = [];
  /** GET responses, keyed by exact URL. */
  private readonly getResponses = new Map<string, unknown>();
  /** POST responses, keyed by URL (any body matches). */
  private readonly postResponses = new Map<string, unknown>();
  /** Default GET response. */
  defaultGetResponse: unknown = null;
  /** Default POST response. */
  defaultPostResponse: unknown = null;
  /** When set, returns throwError(() => err) for the next call. */
  throwOnce: unknown = null;
  /** When set, overrides get() entirely. */
  customGet: <T>(url: string) => Observable<T> | null = null;
  /** When set, overrides post() entirely. */
  customPost: <T>(url: string, body: unknown) => Observable<T> | null = null;

  setGet(url: string, body: unknown): void {
    this.getResponses.set(url, body);
  }
  setPost(url: string, body: unknown): void {
    this.postResponses.set(url, body);
  }

  get<T>(url: string): Observable<T> {
    this.calls.push({ method: 'GET', url, body: undefined });
    if (this.customGet) {
      const ret = this.customGet<T>(url);
      if (ret) return ret;
    }
    if (this.throwOnce) {
      const err = this.throwOnce;
      this.throwOnce = null;
      return throwError(() => err);
    }
    const body = this.getResponses.has(url)
      ? this.getResponses.get(url)
      : this.defaultGetResponse;
    return of(body as T);
  }

  post<T>(url: string, body: unknown): Observable<T> {
    this.calls.push({ method: 'POST', url, body });
    if (this.customPost) {
      const ret = this.customPost<T>(url, body);
      if (ret) return ret;
    }
    if (this.throwOnce) {
      const err = this.throwOnce;
      this.throwOnce = null;
      return throwError(() => err);
    }
    const resp = this.postResponses.has(url)
      ? this.postResponses.get(url)
      : this.defaultPostResponse;
    return of(resp as T);
  }
}

// ── Fixtures (Item 10) ──────────────────────────────────────────────────
// Canonical fixtures live in ./__fixtures__/fixtures.ts. Both the
// component + service spec import from there to keep them in sync.

// ── Testable mirror ──────────────────────────────────────────────────────

/**
 * Mirror of `CheckpointCleanupService`. The real service uses Angular
 * `inject(HttpClient)` — to test without a TestBed we copy the surface
 * verbatim and accept the mock HttpClient via constructor. Each method
 * is a faithful copy; if production drifts, this mirror must drift too.
 */
class TestableCheckpointCleanupService {
  static readonly POLL_INTERVAL_MS = 2000 as const;
  static readonly POLL_MAX_DURATION_MS: number = 10 * 60 * 1000;
  // Item 16 — single-source the availability probe URL; mirrored in
  // the production service. The spec exercises the actual URL by
  // hard-coding it in tests (the test mocks the HTTP layer).
  static readonly AVAILABILITY_URL = '/api/maintenance/checkpoint-cleanup/availability';

  readonly availability = signal<MaintenanceAvailability | null>(null);
  readonly status = signal<CheckpointCleanupStatus | null>(null);
  readonly lastDryRun = signal<CheckpointCleanupDryRun | null>(null);
  readonly lastError = signal<MaintenanceErrorBody | null>(null);
  readonly lastRun = signal<CheckpointCleanupRun | null>(null);

  private readonly API_BASE = '/api/maintenance/checkpoint-cleanup';
  // Allow tests to override the timeout for PR-2 coverage.
  pollMaxMsOverride: number | null = null;

  constructor(private readonly http: MockHttpClient) {}

  fetchAvailability(): Observable<MaintenanceAvailability> {
    return this.http.get<MaintenanceAvailability>(`${this.API_BASE}/availability`).pipe(
      tap((data) => this.availability.set(data)),
      catchError((err) => throwErr(() => this.toErrorBody(err))),
    );
  }

  fetchStatus(): Observable<CheckpointCleanupStatus> {
    return this.http.get<CheckpointCleanupStatus>(`${this.API_BASE}/status`).pipe(
      tap((data) => this.status.set(data)),
      catchError((err) => {
        const body = this.toErrorBody(err);
        this.lastError.set(body);
        return throwErr(() => body);
      }),
    );
  }

  dryRun(): Observable<CheckpointCleanupDryRun> {
    return this.http.post<CheckpointCleanupDryRun>(`${this.API_BASE}/dry-run`, {}).pipe(
      tap((data) => this.lastDryRun.set(data)),
      catchError((err) => {
        const body = this.toErrorBody(err);
        this.lastError.set(body);
        return throwErr(() => body);
      }),
    );
  }

  execute(req: CheckpointCleanupExecuteRequest): Observable<CheckpointCleanupExecute> {
    return this.http
      .post<CheckpointCleanupExecute>(`${this.API_BASE}/execute`, req)
      .pipe(
        catchError((err) => {
          const body = this.toErrorBody(err);
          this.lastError.set(body);
          return throwErr(() => body);
        }),
      );
  }

  getRun(runId: string): Observable<CheckpointCleanupRun> {
    return this.http
      .get<CheckpointCleanupRun>(`${this.API_BASE}/runs/${encodeURIComponent(runId)}`)
      .pipe(
        catchError((err) => {
          const body = this.toErrorBody(err);
          this.lastError.set(body);
          return throwErr(() => body);
        }),
      );
  }

  pollRun(
    runId: string,
    intervalMs: number = TestableCheckpointCleanupService.POLL_INTERVAL_MS,
  ): Observable<CheckpointCleanupRun> {
    const start = Date.now();
    const maxMs = this.pollMaxMsOverride ?? TestableCheckpointCleanupService.POLL_MAX_DURATION_MS;
    return timer(0, intervalMs).pipe(
      switchMap(() => {
        if (Date.now() - start >= maxMs) {
          // Item 4 — the wire body carries the FE-synthesized
          // poll-timeout marker so the display layer can branch
          // into the FE-only `'poll_stale'` sentinel.
          const stuck: MaintenanceErrorBody = {
            error: 'not_initialized',
            message: `Run ${runId} is still in progress after ${Math.round(maxMs / 60000)} min — check daemon logs.`,
            details: { fe_synthesized_poll_timeout: true },
          };
          this.lastError.set(stuck);
          return throwErr(() => stuck);
        }
        return this.getRun(runId).pipe(
          take(1),
          catchError((err) => throwErr(() => err)),
        );
      }),
      tap((run) => this.lastRun.set(run)),
      takeWhile((run) => !this.isTerminalStatus(run.status), true),
    );
  }

  private isTerminalStatus(status: CheckpointCleanupRun['status']): boolean {
    return status === 'succeeded' || status === 'failed' || status === 'interrupted';
  }

  adoptRunIdFromError(body: MaintenanceErrorBody | null): string | null {
    if (!body || body.error !== 'run_in_flight') return null;
    const runId = body.details?.run_id;
    return typeof runId === 'string' && runId.length > 0 ? runId : null;
  }

  toErrorBody(err: unknown): MaintenanceErrorBody {
    const e = err as { error?: Partial<MaintenanceErrorBody> & { error?: string; message?: string }; message?: string };
    const errorBody = (e?.error ?? {}) as Partial<MaintenanceErrorBody> & { error?: string; message?: string; details?: MaintenanceErrorBody['details'] };
    if (
      errorBody &&
      typeof errorBody === 'object' &&
      typeof errorBody.error === 'string' &&
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
    return {
      error: 'not_initialized',
      message:
        (typeof errorBody?.message === 'string' && errorBody.message) ||
        e?.message ||
        'Unknown error',
    };
  }

  clearLastError(): void {
    this.lastError.set(null);
  }

  isDryRunStale(dryRun: CheckpointCleanupDryRun | null, now: number = Date.now()): boolean {
    if (!dryRun) return true;
    const freshUntil = Date.parse(dryRun.fresh_until);
    if (Number.isNaN(freshUntil)) return true;
    return freshUntil <= now;
  }
}

// ── Tests ────────────────────────────────────────────────────────────────

describe('CheckpointCleanupService', () => {
  let http: MockHttpClient;
  let service: TestableCheckpointCleanupService;

  beforeEach(() => {
    http = new MockHttpClient();
    service = new TestableCheckpointCleanupService(http);
  });

  describe('constants', () => {
    it('POLL_INTERVAL_MS = 2000 (T6.3 pin target)', () => {
      expect(TestableCheckpointCleanupService.POLL_INTERVAL_MS).toBe(2000);
    });

    it('POLL_MAX_DURATION_MS is a hard 10-minute timeout (PR-2 fallback)', () => {
      expect(TestableCheckpointCleanupService.POLL_MAX_DURATION_MS).toBe(10 * 60 * 1000);
    });
  });

  describe('fetchAvailability()', () => {
    it('hits GET /availability and updates the availability signal', async () => {
      http.setGet('/api/maintenance/checkpoint-cleanup/availability', AVAILABILITY_READY);
      await firstValueFrom(service.fetchAvailability());
      expect(http.calls).toHaveLength(1);
      expect(http.calls[0]).toEqual({
        method: 'GET',
        url: '/api/maintenance/checkpoint-cleanup/availability',
        body: undefined,
      });
      expect(service.availability()).toEqual(AVAILABILITY_READY);
    });

    it('availability signal carries state for isReady derived computed', () => {
      // The production service exposes `isReady` as a computed — the
      // mirror keeps the signal shape; the spec verifies the signal
      // state directly (the `isReady` derivation is exercised by
      // `app.ts` at runtime).
      service.availability.set(AVAILABILITY_READY);
      expect(service.availability()?.state).toBe('ready');
      service.availability.set({ ...AVAILABILITY_READY, state: 'backend_unsupported' });
      expect(service.availability()?.state).toBe('backend_unsupported');
    });
  });

  describe('fetchStatus()', () => {
    it('hits GET /status and updates the status signal', async () => {
      http.setGet('/api/maintenance/checkpoint-cleanup/status', STATUS);
      await firstValueFrom(service.fetchStatus());
      expect(http.calls).toHaveLength(1);
      expect(http.calls[0].method).toBe('GET');
      expect(http.calls[0].url).toBe('/api/maintenance/checkpoint-cleanup/status');
      expect(service.status()).toEqual(STATUS);
    });

    it('surfaces structured 503 error body via lastError signal', async () => {
      const err = { status: 503, error: { error: 'not_initialized', message: 'not ready' } };
      http.throwOnce = err;
      await expect(firstValueFrom(service.fetchStatus())).rejects.toBeDefined();
      expect(service.lastError()?.error).toBe('not_initialized');
      expect(service.lastError()?.message).toBe('not ready');
    });
  });

  describe('dryRun()', () => {
    it('hits POST /dry-run with empty body {}', async () => {
      http.setPost('/api/maintenance/checkpoint-cleanup/dry-run', DRY_RUN);
      await firstValueFrom(service.dryRun());
      const call = http.calls.find(c => c.method === 'POST' && c.url.endsWith('/dry-run'));
      expect(call).toBeDefined();
      expect(call?.body).toEqual({});
      expect(service.lastDryRun()).toEqual(DRY_RUN);
    });

    it('preserves skipped[] entries on the lastDryRun signal (AM-10)', async () => {
      http.setPost('/api/maintenance/checkpoint-cleanup/dry-run', DRY_RUN);
      await firstValueFrom(service.dryRun());
      // Item 10 — the shared fixture carries 3 skipped entries
      // (ZERO_REFS_FAIL_SAFE + MAX_REFS_EXCEEDED + ERROR:*); the
      // service preserves them all verbatim.
      expect(service.lastDryRun()?.skipped).toHaveLength(3);
      expect(service.lastDryRun()?.skipped[0].reason).toBe('ZERO_REFS_FAIL_SAFE');
    });
  });

  describe('execute() — AM-17 negative pin', () => {
    it('hits POST /execute with EXACTLY {dry_run_run_id, expected_bytes, confirm: true} — no idempotency_key', async () => {
      http.setPost('/api/maintenance/checkpoint-cleanup/execute', EXECUTE_RESP);
      await firstValueFrom(
        service.execute({
          dry_run_run_id: DRY_RUN.run_id,
          expected_bytes: DRY_RUN.would_free_bytes,
          confirm: true,
        }),
      );
      const call = http.calls.find(c => c.method === 'POST' && c.url.endsWith('/execute'));
      expect(call).toBeDefined();
      // EXACT shape pin — AM-17: no idempotency_key.
      expect(call?.body).toEqual({
        dry_run_run_id: DRY_RUN.run_id,
        expected_bytes: DRY_RUN.would_free_bytes,
        confirm: true,
      });
      const keys = Object.keys(call?.body as object).sort();
      expect(keys).toEqual(['confirm', 'dry_run_run_id', 'expected_bytes']);
    });

    it('returns the 202 body with `advisory` and `expected_duration_ms_hint` (AM-12)', async () => {
      http.setPost('/api/maintenance/checkpoint-cleanup/execute', EXECUTE_RESP);
      const body = await firstValueFrom(
        service.execute({
          dry_run_run_id: DRY_RUN.run_id,
          expected_bytes: DRY_RUN.would_free_bytes,
          confirm: true,
        }),
      );
      expect(body.advisory).toBeNull();
      expect(body.expected_duration_ms_hint).toBe(412);
    });
  });

  describe('getRun()', () => {
    it('hits GET /runs/{run_id} with path interpolation', async () => {
      const run = makeRun('running');
      http.setGet('/api/maintenance/checkpoint-cleanup/runs/' + run.run_id, run);
      await firstValueFrom(service.getRun(run.run_id));
      expect(http.calls[0]).toEqual({
        method: 'GET',
        url: '/api/maintenance/checkpoint-cleanup/runs/' + run.run_id,
        body: undefined,
      });
    });

    it('404 → not_found body via lastError signal', async () => {
      http.throwOnce = { status: 404, error: { error: 'not_found', details: { run_id: 'missing' } } };
      await expect(firstValueFrom(service.getRun('missing'))).rejects.toBeDefined();
      expect(service.lastError()?.error).toBe('not_found');
    });
  });

  describe('pollRun() — AM-6 terminal-stop', () => {
    it('emits the terminal value LAST then completes', async () => {
      const running = makeRun('running');
      const succeeded = makeRun('succeeded');
      const queue = [running, succeeded];
      http.customGet = <T>(_url: string) => {
        const next = queue.shift() ?? succeeded;
        return of(next as T);
      };
      const emitted: string[] = [];
      await new Promise<void>((resolve, reject) => {
        service.pollRun(running.run_id, 1).subscribe({
          next: (run) => emitted.push(run.status),
          error: reject,
          complete: resolve,
        });
      });
      // Terminal last — no 'running' AFTER 'succeeded'.
      expect(emitted[emitted.length - 1]).toBe('succeeded');
      const succeededIdx = emitted.indexOf('succeeded');
      expect(emitted.slice(succeededIdx + 1)).toEqual([]);
    });

    it('STOPS on `interrupted` (AM-6 boot-sweep CAS)', async () => {
      const running = makeRun('running');
      const interrupted = makeRun('interrupted');
      const queue = [running, interrupted];
      http.customGet = <T>(_url: string) => {
        const next = queue.shift() ?? interrupted;
        return of(next as T);
      };
      const emitted: string[] = [];
      await new Promise<void>((resolve, reject) => {
        service.pollRun(running.run_id, 1).subscribe({
          next: (run) => emitted.push(run.status),
          error: reject,
          complete: resolve,
        });
      });
      expect(emitted[emitted.length - 1]).toBe('interrupted');
    });

    it('STOPS on `failed`', async () => {
      const failed = makeRun('failed');
      const queue = [makeRun('running'), failed];
      http.customGet = <T>(_url: string) => {
        const next = queue.shift() ?? failed;
        return of(next as T);
      };
      const emitted: string[] = [];
      await new Promise<void>((resolve, reject) => {
        service.pollRun('ckpt-test', 1).subscribe({
          next: (run) => emitted.push(run.status),
          error: reject,
          complete: resolve,
        });
      });
      expect(emitted[emitted.length - 1]).toBe('failed');
    });

    it('POLL_MAX_DURATION_MS fires if the run stays in `running` past the window (PR-2)', async () => {
      const running = makeRun('running');
      http.customGet = <T>(_url: string) => of(running as T);
      // 1ms timeout — the very first re-eval triggers the fallback.
      service.pollMaxMsOverride = 1;
      let errored = false;
      await new Promise<void>((resolve) => {
        service.pollRun('ckpt-test', 1).subscribe({
          next: () => {},
          error: () => { errored = true; resolve(); },
          complete: resolve,
        });
      });
      expect(errored).toBe(true);
      // Item 4 — wire `error: 'not_initialized'` (closest wire-
      // compatible literal; remains a union member). The
      // FE-synthesized poll-timeout marker rides in `details` so
      // the display layer can branch into the FE-only `'poll_stale'`
      // sentinel. The `'poll_stale'` literal is NEVER sent — it is
      // display-only.
      expect(service.lastError()?.error).toBe('not_initialized');
      expect(service.lastError()?.details?.fe_synthesized_poll_timeout).toBe(true);
    });
  });

  describe('adoptRunIdFromError() — AM-14, AM-17', () => {
    it('returns the run_id for a 409 run_in_flight body with details.run_id', () => {
      const body: MaintenanceErrorBody = {
        error: 'run_in_flight',
        details: { run_id: 'ckpt-12345' },
      };
      expect(service.adoptRunIdFromError(body)).toBe('ckpt-12345');
    });

    it('returns null for a 409 run_in_flight body WITHOUT details.run_id', () => {
      const body: MaintenanceErrorBody = { error: 'run_in_flight' };
      expect(service.adoptRunIdFromError(body)).toBeNull();
    });

    it('returns null for a non-run_in_flight error (e.g. byte_count_mismatch)', () => {
      const body: MaintenanceErrorBody = { error: 'byte_count_mismatch' };
      expect(service.adoptRunIdFromError(body)).toBeNull();
    });

    it('returns null for null body', () => {
      expect(service.adoptRunIdFromError(null)).toBeNull();
    });
  });

  describe('toErrorBody() — error mapping', () => {
    it('maps a structured run_in_flight body 1:1', () => {
      const err = {
        status: 409,
        error: { error: 'run_in_flight', details: { run_id: 'ckpt-x' } },
      };
      const body = service.toErrorBody(err);
      expect(body.error).toBe('run_in_flight');
      expect(body.details?.run_id).toBe('ckpt-x');
    });

    it('falls back to {error: "not_initialized", message: ...} on missing body', () => {
      const err = { status: 0, error: null, message: 'Network down' };
      const body = service.toErrorBody(err);
      expect(body.error).toBe('not_initialized');
      expect(body.message).toBe('Network down');
    });

    it('surfaces A-8 `internal_error` verbatim (code + message preserved)', () => {
      const err = {
        status: 500,
        error: { error: 'internal_error', message: 'unexpected boom' },
      };
      const body = service.toErrorBody(err);
      // `internal_error` IS in the union (A-8 amendment) — it passes
      // the type guard and is stored verbatim, with no
      // `not_initialized` coercion.
      expect(body.error).toBe('internal_error');
      expect(body.message).toBe('unexpected boom');
    });

    it('falls back to {error: "not_initialized", message: ...} on a genuinely unknown code', () => {
      const err = {
        status: 502,
        error: { error: 'proxy_gibberish', message: 'Bad gateway' },
      };
      const body = service.toErrorBody(err);
      expect(body.error).toBe('not_initialized');
      expect(body.message).toBe('Bad gateway');
    });
  });

  describe('isDryRunStale()', () => {
    it('returns true when fresh_until is in the past', () => {
      const stale: CheckpointCleanupDryRun = {
        ...DRY_RUN,
        fresh_until: new Date(Date.now() - 60_000).toISOString(),
      };
      expect(service.isDryRunStale(stale)).toBe(true);
    });

    it('returns false when fresh_until is in the future', () => {
      const fresh: CheckpointCleanupDryRun = {
        ...DRY_RUN,
        fresh_until: new Date(Date.now() + 60_000).toISOString(),
      };
      expect(service.isDryRunStale(fresh)).toBe(false);
    });

    it('returns true when dryRun is null', () => {
      expect(service.isDryRunStale(null)).toBe(true);
    });
  });
});

// ── Helpers ──────────────────────────────────────────────────────────────

async function firstValueFrom<T>(o: Observable<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    o.subscribe({
      next: (v) => resolve(v),
      error: reject,
      complete: () => reject(new Error('firstValueFrom: completed before first value')),
    });
  });
}
