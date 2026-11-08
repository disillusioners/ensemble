// SchedulerService — include_cancelled gating (review follow-up 🟡1).
//
// TestBed + HttpTestingController (the house style — the precedent is
// `mcps-gateway.service.spec.ts` → `snapshot.service.spec.ts`): the REAL
// service runs against a mock transport, so the URL, the query params
// and the response flow are exercised against actual production code
// (no logic-mirror drift risk).
//
// THE CONTRACT UNDER TEST: the schedules fetch appends the
// ALREADY-EXISTING backend read param ``?include_cancelled=true``
// (daemon/routers/schedules.py:94 — cancelled rows are HIDDEN by
// default) ONLY when the caller passes true. Toggle OFF must stay a
// bare request with the API's default behavior unchanged — ``false``
// is never sent over the wire.

import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { SchedulerService } from './scheduler.service';
import type { Schedule, ScheduleListResponse } from '../models/scheduler.model';

// ── Fixtures ───────────────────────────────────────────────────────────

function makeSchedule(id: string, status: Schedule['status']): Schedule {
  return {
    id,
    name: id,
    status,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    config: { type: 'one-time', agent: 'ari', message: 'ping' },
  };
}

/** What the backend returns for a BARE request: no cancelled rows. */
const BARE_PAGE: ScheduleListResponse = {
  schedules: [makeSchedule('live-1', 'running'), makeSchedule('done-1', 'stopped')],
};

/** What it returns WITH ?include_cancelled=true: terminal peers ride along. */
const FULL_PAGE: ScheduleListResponse = {
  schedules: [
    makeSchedule('live-1', 'running'),
    makeSchedule('done-1', 'stopped'),
    makeSchedule('cx-1', 'cancelled'),
  ],
};

// ── Suite ──────────────────────────────────────────────────────────────

describe('SchedulerService — listSchedules include_cancelled gating', () => {
  let service: SchedulerService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    });
    service = TestBed.inject(SchedulerService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('no-arg fetch stays BARE: GET /api/schedules with NO include_cancelled param', () => {
    const emitted: Schedule[][] = [];
    service.listSchedules().subscribe((rows) => emitted.push(rows));

    const req = httpMock.expectOne('/api/schedules');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys()).toEqual([]);
    expect(req.request.urlWithParams).toBe('/api/schedules');
    req.flush(BARE_PAGE);

    expect(emitted[0].map((s) => s.id)).toEqual(['live-1', 'done-1']);
  });

  it('explicit false ALSO stays bare — false is never sent over the wire', () => {
    service.listSchedules(false).subscribe();

    const req = httpMock.expectOne('/api/schedules');
    expect(req.request.params.keys()).toEqual([]);
    expect(req.request.urlWithParams).toBe('/api/schedules');
    req.flush(BARE_PAGE);
  });

  it('true appends exactly ?include_cancelled=true (the pre-existing read param)', () => {
    service.listSchedules(true).subscribe();

    const req = httpMock.expectOne('/api/schedules?include_cancelled=true');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('include_cancelled')).toBe('true');
    req.flush(FULL_PAGE);
  });

  it('the included page flows into the schedules signal intact (cancelled rides along)', () => {
    service.listSchedules(true).subscribe();

    httpMock.expectOne('/api/schedules?include_cancelled=true').flush(FULL_PAGE);

    expect(service.schedules().map((s) => s.id)).toEqual(['live-1', 'done-1', 'cx-1']);
    expect(service.schedules().some((s) => s.status === 'cancelled')).toBe(true);
  });
});
