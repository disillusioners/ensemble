import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import {
  provideHttpClientTesting,
  HttpTestingController,
} from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { SnapshotService } from './snapshot.service';
import { HttpParams } from '@angular/common/http';
import { SnapshotFilters } from '../models/snapshot.model';

// ── Factory helpers ─────────────────────────────────────────────

function makeFilters(overrides: Partial<SnapshotFilters> = {}): SnapshotFilters {
  return {
    project_id: null,
    agent_id: null,
    status: [],
    tags: [],
    tag_mode: 'all',
    age: 'all',
    sort: 'created_at_desc',
    limit: 25,
    offset: 0,
    ...overrides,
  };
}

// ── Suite ───────────────────────────────────────────────────────

describe('SnapshotService', () => {
  let service: SnapshotService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        provideNoopAnimations(),
      ],
    });
    service = TestBed.inject(SnapshotService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  // ── (a) buildParams — multi-value tags + status + D-2 wire rename
  it('encodes single + repeated tags and status, and maps agent_id to the agent wire param (D-2)', () => {
    const params = service.buildParams(
      makeFilters({
        agent_id: 'coder',
        status: ['active', 'failed'],
        tags: ['domain:api', 'runtime:py'],
      }),
    );
    const all = params.toString();
    // D-2 — wire name is `agent`
    expect(all).toContain('agent=coder');
    expect(all).not.toContain('agent_id=');
    // Status — repeat params
    expect(all).toContain('status=active');
    expect(all).toContain('status=failed');
    // Tags — repeat params (HttpParams does NOT encode ':' by default;
    // tag values are 'dim:value' strings that the BE accepts raw).
    expect(all).toContain('tags=domain:api');
    expect(all).toContain('tags=runtime:py');
  });

  // ── (b) tag_mode toggling — emits only when non-default
  it('emits tag_mode=any when the filter is "any" and omits it when "all" (default)', () => {
    const def = service.buildParams(makeFilters({ tag_mode: 'all' }));
    expect(def.toString()).not.toContain('tag_mode');

    const any = service.buildParams(makeFilters({ tag_mode: 'any' }));
    expect(any.toString()).toContain('tag_mode=any');
  });

  // ── (c) computeAgeCutoff — 24h returns ISO; "all" returns null
  it('returns an ISO string 24h ago for "24h" and null for "all"', () => {
    const now = new Date('2026-10-05T22:00:00.000Z');
    const iso24 = service.computeAgeCutoff('24h', now);
    expect(iso24).not.toBeNull();
    expect(new Date(iso24!).toISOString()).toBe('2026-10-04T22:00:00.000Z');

    const all = service.computeAgeCutoff('all', now);
    expect(all).toBeNull();
  });

  it('returns the correct cutoff for "7d" and "30d"', () => {
    const now = new Date('2026-10-05T22:00:00.000Z');
    expect(new Date(service.computeAgeCutoff('7d', now)!).toISOString()).toBe(
      '2026-09-28T22:00:00.000Z',
    );
    expect(new Date(service.computeAgeCutoff('30d', now)!).toISOString()).toBe(
      '2026-09-05T22:00:00.000Z',
    );
  });

  // ── (d) list() — calls LIST_URL with the encoded params
  it('list() calls LIST_URL with the encoded params and returns the response', (done) => {
    const filters = makeFilters({
      project_id: 'default',
      status: ['active'],
      tags: ['domain:api'],
      tag_mode: 'any',
      age: '7d',
      sort: 'title_asc',
      limit: 10,
      offset: 20,
    });
    service.list(filters).subscribe({
      next: (resp) => {
        expect(resp).toBeTruthy();
        done();
      },
      error: done.fail,
    });
    const req = httpMock.expectOne((r) => r.url === SnapshotService.LIST_URL);
    expect(req.request.method).toBe('GET');
    // Spot-check the URL has every key the filter shape emits
    const url = req.request.urlWithParams;
    expect(url).toContain('project_id=default');
    expect(url).toContain('status=active');
    expect(url).toContain('tags=domain:api');
    expect(url).toContain('tag_mode=any');
    expect(url).toContain('created_after='); // age=7d → created_after via computeAgeCutoff
    expect(url).toContain('sort=title_asc');
    expect(url).toContain('limit=10');
    expect(url).toContain('offset=20');
    req.flush({ items: [], total: 0 });
  });

  // ── (e) getById — appends include=digest when opted in
  it('getById(id, { includeDigest: true }) appends ?include=digest; includeDigest: false omits the param', () => {
    let firstUrl = '';
    service.getById('snap-1', { includeDigest: true }).subscribe();
    const req1 = httpMock.expectOne((r) => r.url === `${SnapshotService.DETAIL_URL}/snap-1`);
    firstUrl = req1.request.urlWithParams;
    expect(firstUrl).toContain('include=digest');
    req1.flush({ id: 'snap-1', digest: {} });

    let secondUrl = '';
    service.getById('snap-2', { includeDigest: false }).subscribe();
    const req2 = httpMock.expectOne((r) => r.url === `${SnapshotService.DETAIL_URL}/snap-2`);
    secondUrl = req2.request.urlWithParams;
    expect(secondUrl).not.toContain('include');
    req2.flush({ id: 'snap-2', digest: {} });
  });

  // ── (f) getMetrics() — calls METRICS_URL
  it('getMetrics() calls METRICS_URL and caches the response in the metrics signal', (done) => {
    expect(service.metrics()).toBeNull();
    service.getMetrics().subscribe({
      next: (m) => {
        expect(m).toEqual({
          capture_counts: { coder: { created: 5 } },
          spawn_counts_per_snapshot: [],
        });
        expect(service.metrics()).toEqual(m);
        done();
      },
      error: done.fail,
    });
    const req = httpMock.expectOne((r) => r.url === SnapshotService.METRICS_URL);
    expect(req.request.method).toBe('GET');
    req.flush({
      capture_counts: { coder: { created: 5 } },
      spawn_counts_per_snapshot: [],
    });
  });
});
