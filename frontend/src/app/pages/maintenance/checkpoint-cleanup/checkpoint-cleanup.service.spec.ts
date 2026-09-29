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
import { Observable, EMPTY, Subscription, catchError, concat, defer, expand, of, switchMap, take, takeUntil, takeWhile, tap, throwError, timer } from 'rxjs';
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
  // Fix commission 2026-09-29 — replaced `POLL_MAX_DURATION_MS`
  // (hard 10-min cap → synthesized poll-timeout error) with the
  // hint-derived budget constants + backoff helpers. The
  // post-cap backoff phase keeps polling until terminal; no
  // synthesized error.
  static readonly HINT_MULTIPLIER = 2 as const;
  static readonly POLL_BUDGET_FLOOR_MS: number = 15 * 60 * 1000;
  static readonly POLL_BUDGET_CEILING_MS: number = 60 * 60 * 1000;
  static readonly BACKOFF_STEP_MS: number = 2000;
  static readonly BACKOFF_CEILING_MS: number = 30_000;

  // MINOR-1 (iter3) — mirrored clamp: huge-but-finite hints must
  // produce a FINITE budget (product Infinity would make
  // `timer(budgetMs)` never fire, starving the BACKOFF phase).
  static computePollBudgetMs(hintMs: number | null | undefined): number {
    if (typeof hintMs === 'number' && Number.isFinite(hintMs) && hintMs > 0) {
      return Math.min(
        Math.max(
          hintMs * TestableCheckpointCleanupService.HINT_MULTIPLIER,
          TestableCheckpointCleanupService.POLL_BUDGET_FLOOR_MS,
        ),
        TestableCheckpointCleanupService.POLL_BUDGET_CEILING_MS,
      );
    }
    return TestableCheckpointCleanupService.POLL_BUDGET_FLOOR_MS;
  }

  static computeBackoffMs(
    tickIdx: number,
    intervalMs: number = TestableCheckpointCleanupService.POLL_INTERVAL_MS,
  ): number {
    const base =
      intervalMs + tickIdx * TestableCheckpointCleanupService.BACKOFF_STEP_MS;
    return Math.min(base, TestableCheckpointCleanupService.BACKOFF_CEILING_MS);
  }

  // Item 16 — single-source the availability probe URL; mirrored in
  // the production service. The spec exercises the actual URL by
  // hard-coding it in tests (the test mocks the HTTP layer).
  static readonly AVAILABILITY_URL = '/api/maintenance/checkpoint-cleanup/availability';

  readonly status = signal<CheckpointCleanupStatus | null>(null);
  readonly lastDryRun = signal<CheckpointCleanupDryRun | null>(null);
  readonly lastError = signal<MaintenanceErrorBody | null>(null);
  // Item 12 — `availability` / `lastRun` are dead surface in
  // production (gear-menu probe + canMatch guard hit the URL
  // directly; the caller owns the terminal row). Spec mirror also
  // drops them; the spec keeps `fetchAvailability()` as a deprecated
  // no-op shell so the test block doesn't break in this pass.

  private readonly API_BASE = '/api/maintenance/checkpoint-cleanup';
  // Allow tests to override the budget sizing for the post-cap
  // continuation tests. The default is the production
  // `computePollBudgetMs`; setting it to a small ms (e.g. 10) makes
  // the active phase exhaust almost immediately, exercising the
  // backoff transition with tight fake-timer budgets.
  pollBudgetOverrideMs: number | null = null;

  constructor(private readonly http: MockHttpClient) {}

  /** @deprecated Item 12 — see production service; URL-only probe. */
  fetchAvailability(): Observable<MaintenanceAvailability> {
    return this.http.get<MaintenanceAvailability>(`${this.API_BASE}/availability`).pipe(
      catchError((err) => throwError(() => this.toErrorBody(err))),
    );
  }

  fetchStatus(): Observable<CheckpointCleanupStatus> {
    return this.http.get<CheckpointCleanupStatus>(`${this.API_BASE}/status`).pipe(
      tap((data) => this.status.set(data)),
      catchError((err) => {
        const body = this.toErrorBody(err);
        this.lastError.set(body);
        return throwError(() => body);
      }),
    );
  }

  dryRun(): Observable<CheckpointCleanupDryRun> {
    return this.http.post<CheckpointCleanupDryRun>(`${this.API_BASE}/dry-run`, {}).pipe(
      tap((data) => this.lastDryRun.set(data)),
      catchError((err) => {
        const body = this.toErrorBody(err);
        this.lastError.set(body);
        return throwError(() => body);
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
          return throwError(() => body);
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
          return throwError(() => body);
        }),
      );
  }

  pollRun(
    runId: string,
    intervalMs: number = TestableCheckpointCleanupService.POLL_INTERVAL_MS,
    hintMs: number | null = null,
  ): Observable<CheckpointCleanupRun> {
    const budgetMs =
      this.pollBudgetOverrideMs ??
      TestableCheckpointCleanupService.computePollBudgetMs(hintMs);

    // Mirrors production: ACTIVE phase — timer at t=0 then every
    // intervalMs, takeWhile inclusive on terminal, takeUntil budget.
    // The takeWhile predicate is also the side-effect site that
    // flips `activeSawTerminal` so the BACKOFF phase can
    // short-circuit when terminal was already emitted.
    let activeSawTerminal = false;
    const activePhase = timer(0, intervalMs).pipe(
      switchMap(() => this.getRun(runId).pipe(take(1))),
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
      takeUntil(timer(budgetMs)),
    );

    // BACKOFF phase — defer-gated expand() so it only seeds when
    // the ACTIVE phase has fully completed (budget elapsed AND
    // still non-terminal). Mirrors production's recursive ramp
    // `intervalMs + tickIdx × BACKOFF_STEP_MS`, clamped at the
    // ceiling. If ACTIVE already saw terminal, defer returns
    // EMPTY — no extra fetch, no duplicate terminal emission.
    const backoffPhase = defer(() => {
      if (activeSawTerminal) {
        return EMPTY;
      }
      return timer(intervalMs).pipe(
        // MAJOR-1 (iter3) — mirrored fix: bind expand's SECOND
        // projection arg (the recurrence index), not the emitted
        // value (the seed timer always emits 0, so the old
        // value-binding pinned every gap at computeBackoffMs(0) —
        // constant cadence, dead ramp). See the cadence-pinning
        // tests below.
        expand((_, tickIdx) =>
          timer(TestableCheckpointCleanupService.computeBackoffMs(tickIdx, intervalMs)),
        ),
        switchMap(() => this.getRun(runId).pipe(take(1))),
        takeWhile((run) => !this.isTerminalStatus(run.status), true),
      );
    });

    return concat(activePhase, backoffPhase);
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

  describe('constants — fix commission 2026-09-29 (budget + backoff)', () => {
    it('POLL_INTERVAL_MS = 2000 (T6.3 pin target)', () => {
      expect(TestableCheckpointCleanupService.POLL_INTERVAL_MS).toBe(2000);
    });

    it('HINT_MULTIPLIER = 2 (budget hint sizing)', () => {
      expect(TestableCheckpointCleanupService.HINT_MULTIPLIER).toBe(2);
    });

    it('POLL_BUDGET_FLOOR_MS = 15 minutes (above measured worst case ~12m)', () => {
      expect(TestableCheckpointCleanupService.POLL_BUDGET_FLOOR_MS).toBe(15 * 60 * 1000);
    });

    it('BACKOFF_STEP_MS = 2000 (linear ramp step)', () => {
      expect(TestableCheckpointCleanupService.BACKOFF_STEP_MS).toBe(2000);
    });

    it('BACKOFF_CEILING_MS = 30_000 (post-cap cadence cap)', () => {
      expect(TestableCheckpointCleanupService.BACKOFF_CEILING_MS).toBe(30_000);
    });

    it('POLL_BUDGET_CEILING_MS = 60 min (MINOR-1 overflow clamp; ≥ floor, finite)', () => {
      expect(TestableCheckpointCleanupService.POLL_BUDGET_CEILING_MS).toBe(3_600_000);
      expect(TestableCheckpointCleanupService.POLL_BUDGET_CEILING_MS)
        .toBeGreaterThanOrEqual(TestableCheckpointCleanupService.POLL_BUDGET_FLOOR_MS);
    });
  });

  describe('computePollBudgetMs() — pure helper (fix commission 2026-09-29)', () => {
    it('hint > 0 → max(hint × N, floor)', () => {
      // hint=5 min (300_000 ms) → 600_000 ms capped to floor = 900_000 ms
      expect(TestableCheckpointCleanupService.computePollBudgetMs(300_000)).toBe(900_000);
    });

    it('hint × N > floor → multiplier wins (large hints)', () => {
      // hint=10 min (600_000 ms) → 1_200_000 ms (> floor)
      expect(TestableCheckpointCleanupService.computePollBudgetMs(600_000)).toBe(1_200_000);
    });

    it('hint × N < floor → floor wins (small hints)', () => {
      // hint=1 min (60_000 ms) → 120_000 ms, floor wins
      expect(TestableCheckpointCleanupService.computePollBudgetMs(60_000)).toBe(900_000);
    });

    it('hint = 0 → floor (legacy execute no hint)', () => {
      expect(TestableCheckpointCleanupService.computePollBudgetMs(0)).toBe(900_000);
    });

    it('hint = null → floor (re-entry no hint)', () => {
      expect(TestableCheckpointCleanupService.computePollBudgetMs(null)).toBe(900_000);
    });

    it('hint = undefined → floor', () => {
      expect(TestableCheckpointCleanupService.computePollBudgetMs(undefined)).toBe(900_000);
    });

    it('hint = NaN → floor (defensive against malformed bodies)', () => {
      expect(TestableCheckpointCleanupService.computePollBudgetMs(NaN)).toBe(900_000);
    });

    it('hint = Infinity → floor (defensive: Number.isFinite gates positive finite hints)', () => {
      // `Number.isFinite(Infinity)` is false — the helper
      // rejects non-finite values and falls back to the floor.
      // Defensive against malformed bodies that leak Infinity
      // (e.g., wire corruption or NaN propagation).
      expect(TestableCheckpointCleanupService.computePollBudgetMs(Infinity)).toBe(900_000);
    });

    it('hint = 1e10 → clamped to POLL_BUDGET_CEILING_MS (MINOR-1: finite budget that still yields to backoff)', () => {
      // 1e10 × 2 = 2e10 — finite but absurd; without the ceiling
      // clamp the ACTIVE phase would hold the active cadence for
      // ~231 days before ever yielding to backoff. The clamp keeps
      // the budget finite AND bounded.
      expect(TestableCheckpointCleanupService.computePollBudgetMs(1e10)).toBe(
        TestableCheckpointCleanupService.POLL_BUDGET_CEILING_MS,
      );
    });

    it('hint = Number.MAX_VALUE → product = Infinity → clamped to POLL_BUDGET_CEILING_MS (MINOR-1: timer(budget) must always fire)', () => {
      // The hazard, pinned: a huge-but-finite hint makes the
      // hint × HINT_MULTIPLIER product Infinity (Number.isFinite
      // does NOT gate it), and `timer(Infinity)` never fires — the
      // ACTIVE phase would never yield to BACKOFF. The clamp is the
      // fix; the yield-to-backoff behavior is proven end-to-end in
      // the pollRun() MINOR-1 test below.
      expect(Number.MAX_VALUE * 2).toBe(Infinity);
      expect(TestableCheckpointCleanupService.computePollBudgetMs(Number.MAX_VALUE)).toBe(
        TestableCheckpointCleanupService.POLL_BUDGET_CEILING_MS,
      );
    });

    it('hint = negative → floor (defensive against malformed bodies)', () => {
      expect(TestableCheckpointCleanupService.computePollBudgetMs(-5_000)).toBe(900_000);
    });
  });

  describe('computeBackoffMs() — pure helper (fix commission 2026-09-29)', () => {
    it('tickIdx 0 → POLL_INTERVAL_MS (start of ramp)', () => {
      expect(TestableCheckpointCleanupService.computeBackoffMs(0)).toBe(2000);
    });

    it('tickIdx 1 → POLL_INTERVAL_MS + step (4 s)', () => {
      expect(TestableCheckpointCleanupService.computeBackoffMs(1)).toBe(4000);
    });

    it('tickIdx 14 → ceiling (last linear step)', () => {
      expect(TestableCheckpointCleanupService.computeBackoffMs(14)).toBe(30_000);
    });

    it('tickIdx ≥ 14 → ceiling (clamped)', () => {
      expect(TestableCheckpointCleanupService.computeBackoffMs(15)).toBe(30_000);
      expect(TestableCheckpointCleanupService.computeBackoffMs(100)).toBe(30_000);
    });
  });

  // ── MAJOR-1 regression (iter3) — BACKOFF-phase cadence pinning ─────────
  //
  // MAJOR-1 shipped because NO test pinned the BACKOFF cadence: the
  // old `expand((tickIdx) => timer(...))` bound the EMITTED VALUE
  // (always 0 from the seed timer) instead of the recurrence index,
  // collapsing every inter-emission gap to
  // `computeBackoffMs(0, intervalMs)` = intervalMs — a constant 2-s
  // cadence with the ramp and the 30-s ceiling dead. These tests
  // advance the fake clock PER GAP and pin each BACKOFF
  // inter-emission gap to `computeBackoffMs(n, 2000)` — the JSDoc
  // cadence contract: 2 s (outer seed), 2 s (idx 0), 4 s (idx 1),
  // 6 s (idx 2), …, 30 s ceiling first reached at idx 14, clamped
  // at 30 s beyond.
  describe('pollRun() BACKOFF cadence — MAJOR-1 regression (iter3: expand index binding)', () => {
    /**
     * Drain the ACTIVE phase against fake timers and land at the
     * BACKOFF seed. `pollBudgetOverrideMs = 1` + `intervalMs` gives
     * exactly one active emission (t=0) before the budget
     * terminator (t=1); the BACKOFF defer then seeds its outer
     * `timer(intervalMs)`. Returns the emission recorder.
     */
    async function drainToBackoffSeed(
      svc: TestableCheckpointCleanupService,
      http: MockHttpClient,
      intervalMs: number,
    ): Promise<{ sub: Subscription; emissionTimes: number[] }> {
      const running = makeRun('running');
      // Never terminal — the BACKOFF phase must keep polling until
      // WE advance the clock, so every gap is observable.
      http.customGet = <T>(_url: string) => of(running as T);
      svc.pollBudgetOverrideMs = 1;
      const emissionTimes: number[] = [];
      const sub = svc
        .pollRun('ckpt-cadence', intervalMs, null)
        .subscribe({ next: () => emissionTimes.push(Date.now()) });
      // t=0 — the only ACTIVE tick inside the 1-ms budget.
      await jest.advanceTimersToNextTimerAsync();
      // t=1 — budget terminator fires → ACTIVE yields → BACKOFF
      // defer seeds the outer `timer(intervalMs)`.
      await jest.advanceTimersByTimeAsync(1);
      expect(emissionTimes.length).toBe(1);
      // BACKOFF poll #1 — the outer seed. NOT gap-pinned: the seed
      // timer is scheduled at t=1 (after the terminator), so the
      // ACTIVE→BACKOFF transition gap carries +1 ms of terminator
      // skew. Presence only.
      await jest.advanceTimersByTimeAsync(intervalMs);
      expect(emissionTimes.length).toBe(2);
      return { sub, emissionTimes };
    }

    /** Advance exactly one expected gap; assert one emission landed and the gap matches. */
    async function pinGap(
      emissionTimes: number[],
      tickIdx: number,
      expectedGapMs: number,
    ): Promise<void> {
      // Cross-check the table against the helper under test.
      expect(TestableCheckpointCleanupService.computeBackoffMs(tickIdx, 2000)).toBe(expectedGapMs);
      const before = emissionTimes.length;
      await jest.advanceTimersByTimeAsync(expectedGapMs);
      expect(emissionTimes.length).toBe(before + 1);
      const gap = emissionTimes[emissionTimes.length - 1] - emissionTimes[emissionTimes.length - 2];
      expect(gap).toBe(expectedGapMs);
    }

    it('early ramp pinned: BACKOFF gaps are exactly 2 s (idx 0), 4 s (idx 1), 6 s (idx 2) — full JSDoc cadence 2 s, 2 s, 4 s, 6 s', async () => {
      jest.useFakeTimers();
      let sub: Subscription | null = null;
      try {
        const { sub: s, emissionTimes } = await drainToBackoffSeed(service, http, 2000);
        sub = s;
        // JSDoc gap table: gap 1 = outer seed 2 s (asserted in
        // drainToBackoffSeed by advancing exactly intervalMs and
        // requiring exactly one emission); gaps 2-4 below.
        await pinGap(emissionTimes, 0, 2000); // gap 2 — expand idx 0
        await pinGap(emissionTimes, 1, 4000); // gap 3 — expand idx 1
        await pinGap(emissionTimes, 2, 6000); // gap 4 — expand idx 2
      } finally {
        sub?.unsubscribe();
        jest.useRealTimers();
      }
    });

    it('ceiling pin: the ramp reaches the 30-s ceiling exactly at tickIdx 14 (idx 13 → 28 s) and clamps at 30 s beyond (idx 14, 15)', async () => {
      jest.useFakeTimers();
      let sub: Subscription | null = null;
      try {
        const { sub: s, emissionTimes } = await drainToBackoffSeed(service, http, 2000);
        sub = s;
        // Walk the linear ramp to the last pre-ceiling step.
        // Ceiling must NOT have been reached at idx 13.
        expect(TestableCheckpointCleanupService.computeBackoffMs(13, 2000)).toBe(28_000);
        for (const [tickIdx, gapMs] of [
          [0, 2000], [1, 4000], [2, 6000], [3, 8000], [4, 10_000],
          [5, 12_000], [6, 14_000], [7, 16_000], [8, 18_000], [9, 20_000],
          [10, 22_000], [11, 24_000], [12, 26_000], [13, 28_000],
        ] as Array<[number, number]>) {
          await pinGap(emissionTimes, tickIdx, gapMs);
        }
        // Ceiling FIRST reached at tickIdx 14 → exactly 30 s …
        await pinGap(emissionTimes, 14, 30_000);
        // … and CLAMPED beyond: idx 15 also 30 s (not 32 s).
        await pinGap(emissionTimes, 15, 30_000);
      } finally {
        sub?.unsubscribe();
        jest.useRealTimers();
      }
    });

    it('MINOR-1: a Number.MAX_VALUE hint clamps to POLL_BUDGET_CEILING_MS and the ACTIVE phase still yields to BACKOFF (timer(budget) fires)', async () => {
      jest.useFakeTimers();
      let sub: Subscription | null = null;
      try {
        const running = makeRun('running');
        http.customGet = <T>(_url: string) => of(running as T); // never terminal
        // Production budget path (NO override): hint=Number.MAX_VALUE
        // → hint × 2 = Infinity → MUST clamp to the ceiling, else
        // `timer(Infinity)` never fires and BACKOFF is starved
        // forever. intervalMs=60_000 keeps the tick count low.
        const emissionTimes: number[] = [];
        sub = service
          .pollRun('ckpt-minor1', 60_000, Number.MAX_VALUE)
          .subscribe({ next: () => emissionTimes.push(Date.now()) });
        await jest.advanceTimersToNextTimerAsync(); // t=0 active tick
        expect(emissionTimes.length).toBe(1);
        // Cross the clamped budget (3_600_000 ms): the terminator
        // MUST fire → ACTIVE yields…
        await jest.advanceTimersByTimeAsync(3_600_000);
        const atBoundary = emissionTimes.length;
        expect(atBoundary).toBeGreaterThan(1); // active ticks across the window
        // …and BACKOFF seeded: outer `timer(60_000)` poll lands.
        await jest.advanceTimersByTimeAsync(60_000);
        expect(emissionTimes.length).toBe(atBoundary + 1);
      } finally {
        sub?.unsubscribe();
        jest.useRealTimers();
      }
    });
  });

  describe('fetchAvailability() — Item 12 deprecated shell', () => {
    it('hits GET /availability (URL contract)', async () => {
      // The production service exposes the URL constant + the
      // canMatch guard / app.ts gear-menu probe hit it directly.
      // The fetchAvailability() wrapper is @deprecated; this test
      // pins the URL contract (canonical literal matches the
      // single-source constant).
      http.setGet('/api/maintenance/checkpoint-cleanup/availability', AVAILABILITY_READY);
      await firstValueFrom(service.fetchAvailability());
      expect(http.calls).toHaveLength(1);
      expect(http.calls[0]).toEqual({
        method: 'GET',
        url: '/api/maintenance/checkpoint-cleanup/availability',
        body: undefined,
      });
    });

    it('AVAILABILITY_URL static constant matches the URL literal', () => {
      expect(TestableCheckpointCleanupService.AVAILABILITY_URL)
        .toBe('/api/maintenance/checkpoint-cleanup/availability');
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

    it('post-budget: polling CONTINUES past the budget until terminal, terminal value LAST, no synthesized error (fix commission 2026-09-29, replaces PR-2 timeout; renamed iter3 — cadence is pinned separately by the MAJOR-1 cadence describe)', async () => {
      // The active phase uses a 1 ms budget (override) so it
      // exhausts on the first tick(s). The run stays non-terminal
      // for several backoff polls, then becomes succeeded. The
      // stream MUST NOT error; polling continues into the
      // BACKOFF phase; the terminal value emits LAST.
      //
      // iter3 rename — the old label ("keeps polling with
      // backoff") implied a cadence assertion this test never
      // made; that coverage gap is exactly how MAJOR-1 shipped.
      // Cadence is now pinned by the
      // `pollRun() BACKOFF cadence — MAJOR-1 regression` describe
      // above.
      //
      // iter3 timing fix — with the CORRECTED ramp the backoff
      // gaps grow past jest's real-time timeout (6 s at idx 3,
      // 8 s at idx 4 on a 1-ms interval), so this test drives the
      // stream on JEST FAKE TIMERS: one 30-s virtual advance
      // walks the whole ACTIVE→BACKOFF→terminal sequence
      // deterministically, in milliseconds of wall time.
      const running = makeRun('running');
      const succeeded = makeRun('succeeded');
      const emitted: string[] = [];
      // Multi-stage queue: first poll(s) running (active), several
      // running (backoff ramp), then succeeded.
      const queue: CheckpointCleanupRun[] = [
        running,
        running, running, running, running, running,
        succeeded,
      ];
      http.customGet = <T>(_url: string) => {
        const next = queue.shift() ?? succeeded;
        return of(next as T);
      };
      jest.useFakeTimers();
      try {
        service.pollBudgetOverrideMs = 1;
        const completed = new Promise<void>((resolve, reject) => {
          service.pollRun('ckpt-test', 1, null).subscribe({
            next: (run) => emitted.push(run.status),
            error: (err) => reject(new Error(`unexpected error: ${JSON.stringify(err)}`)),
            complete: resolve,
          });
        });
        // Walk the entire stream in virtual time: active tick(s) at
        // t=0/1 ms, budget terminator, backoff seed, then the ramped
        // gaps (1, 2001, 4001, 6001, 8001 ms) until the queue's
        // terminal entry completes the stream. 30 s covers it all.
        await jest.advanceTimersByTimeAsync(30_000);
        await completed;
      } finally {
        jest.useRealTimers();
      }
      // Terminal LAST — backoff preserved the active-phase takeWhile
      // inclusive semantics through the phase transition.
      expect(emitted[emitted.length - 1]).toBe('succeeded');
      const succeededIdx = emitted.indexOf('succeeded');
      expect(emitted.slice(succeededIdx + 1)).toEqual([]);
      // The OLD contract errored out with `fe_synthesized_poll_timeout`;
      // the NEW contract MUST NOT synthesize any lastError body for the
      // post-cap continuation case.
      expect(service.lastError()).toBeNull();
      // The post-cap continuation MUST poll past the budget —
      // i.e., several backoff-phase fetches fired (≥ 3 running
      // emissions after the initial active-phase tick).
      const runningCount = emitted.filter((s) => s === 'running').length;
      expect(runningCount).toBeGreaterThanOrEqual(4);
    });

    it('post-budget: a run that terminals on the FIRST poll completes immediately regardless of a hint-sized budget (MINOR-4 rename, iter3 — budget sizing itself is pinned by the computePollBudgetMs tests)', async () => {
      // What this actually exercises: with `pollBudgetOverrideMs`
      // cleared and a real hint (10-min hint × 2 → 20-min active
      // budget), the run is ALREADY terminal on the first poll —
      // `takeWhile(..., true)` short-circuits and the stream
      // completes immediately. The budget value never comes into
      // play (no ~20-min wait, no backoff transition); the smoke
      // proof is that the terminal value emits LAST with no
      // synthesized error. The budget MATH is pinned by the
      // `computePollBudgetMs()` describe above.
      const succeeded = makeRun('succeeded');
      http.customGet = <T>(_url: string) => of(succeeded as T);
      const emitted: string[] = [];
      await new Promise<void>((resolve, reject) => {
        service.pollRun('ckpt-test', 1, 600_000).subscribe({
          next: (run) => emitted.push(run.status),
          error: reject,
          complete: resolve,
        });
      });
      expect(emitted[emitted.length - 1]).toBe('succeeded');
      expect(service.lastError()).toBeNull();
    });

    it('post-budget: poll error (404) propagates; ACTIVE phase is not retried via BACKOFF', async () => {
      // `concat(activePhase, backoffPhase)` does NOT subscribe to
      // the second observable if the first errors. The
      // `fe_synthesized_poll_timeout` synthesis is gone, so any
      // error is a genuine BE error (404, network, etc.).
      http.throwOnce = {
        status: 404,
        error: { error: 'not_found', details: { run_id: 'ckpt-test' } },
      };
      let errored = false;
      let lastErr: MaintenanceErrorBody | null = null;
      await new Promise<void>((resolve) => {
        service.pollRun('ckpt-test', 1, null).subscribe({
          next: () => {},
          error: (err: MaintenanceErrorBody) => {
            errored = true;
            lastErr = err;
            resolve();
          },
          complete: resolve,
        });
      });
      expect(errored).toBe(true);
      expect(lastErr?.error).toBe('not_found');
      // No poll-timeout synthesis — the marker MUST NOT be set
      // for a BE 404.
      expect(lastErr?.details?.fe_synthesized_poll_timeout).toBeUndefined();
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
