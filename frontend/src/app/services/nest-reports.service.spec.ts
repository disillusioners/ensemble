// The NEST-REPORTS client's spec (the nest-anomaly lane, phase 4).
//
// The load-bearing pins:
//
//   * the URL shapes — RELATIVE `/api/nest-reports/...` paths (the door
//     is same-origin) and the detail id is `encodeURIComponent`-ed;
//   * `Accept: application/json` is LOAD-BEARING (the auth middleware
//     302-redirects a wildcard-Accept request to an HTML login page);
//   * the 15 s client deadline maps to the typed timeout error;
//   * the query params are forwarded ONLY when set — and
//     `include_excerpt` ONLY when the operator asks (AM-12b).

import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';

import { NestReportsService } from './nest-reports.service';
import { MetricsDoorTimeoutError } from '../models/metrics.model';

describe('NestReportsService', () => {
  let service: NestReportsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(NestReportsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('asks the RELATIVE clusters URL and sends the JSON accept header', () => {
    let seen = '';
    service.getClusters().subscribe((payload) => {
      seen = payload.generated_at_utc;
    });
    const request = http.expectOne(NestReportsService.CLUSTERS_URL);
    expect(request.request.method).toBe('GET');
    expect(request.request.headers.get('Accept')).toBe('application/json');
    expect(request.request.headers.get('Accept')).not.toContain('*');
    request.flush({
      groups: [],
      bound: {
        matched_count: 0,
        returned_count: 0,
        dropped_count: 0,
        more_available: false,
        truncated: false,
        narrow_hint: null,
      },
      category_breakdown: {},
      freshness: {
        window_s: 3600,
        window_cutoff_utc: '2026-11-05T00:00:00+00:00',
        queried_at_utc: '2026-11-05T01:00:00+00:00',
        resolve_window_s: 3600,
        line: 'clusters as of 2026-11-05T01:00:00+00:00 (server clock)',
      },
      generated_at_utc: '2026-11-05T01:00:00+00:00',
    });
    expect(seen).toBe('2026-11-05T01:00:00+00:00');
  });

  it('forwards the query params ONLY when they are set', () => {
    service.getClusters().subscribe();
    const bare = http.expectOne(NestReportsService.CLUSTERS_URL);
    expect(bare.request.params.keys().length).toBe(0);
    bare.flush({});

    service.getClusters({ windowS: 900, severity: 'major', category: 'tool-failure', limit: 5 }).subscribe();
    const full = http.expectOne(
      (request) => request.url === NestReportsService.CLUSTERS_URL,
    );
    expect(full.request.params.get('window_s')).toBe('900');
    expect(full.request.params.get('severity')).toBe('major');
    expect(full.request.params.get('category')).toBe('tool-failure');
    expect(full.request.params.get('limit')).toBe('5');
    full.flush({});
  });

  it('the detail URL encodes the cluster id', () => {
    expect(service.clusterDetailUrl('cl-1')).toBe('/api/nest-reports/clusters/cl-1');
    expect(service.clusterDetailUrl('a b/c?d')).toBe('/api/nest-reports/clusters/a%20b%2Fc%3Fd');
  });

  it('the excerpt is forwarded ONLY when the operator asks (AM-12b)', () => {
    service.getClusterDetail('cl-1').subscribe();
    const plain = http.expectOne((request) => request.url === '/api/nest-reports/clusters/cl-1');
    expect(plain.request.params.has('include_excerpt')).toBe(false);
    plain.flush({});

    service.getClusterDetail('cl-1', { includeExcerpt: true }).subscribe();
    const withExcerpt = http.expectOne(
      (request) => request.url === '/api/nest-reports/clusters/cl-1',
    );
    expect(withExcerpt.request.params.get('include_excerpt')).toBe('true');
    withExcerpt.flush({});
  });

  it('the deadline maps to the typed timeout error', () => {
    jest.useFakeTimers();
    let failure: unknown = null;
    service.getClusters().subscribe({
      next: () => undefined,
      error: (err: unknown) => {
        failure = err;
      },
    });
    http.expectOne(NestReportsService.CLUSTERS_URL);
    jest.advanceTimersByTime(NestReportsService.DOOR_TIMEOUT_MS + 1);
    expect(failure).toBeInstanceOf(MetricsDoorTimeoutError);
    jest.useRealTimers();
  });

  it('the deadline is the house 15 s budget', () => {
    expect(NestReportsService.DOOR_TIMEOUT_MS).toBe(15_000);
  });
});
