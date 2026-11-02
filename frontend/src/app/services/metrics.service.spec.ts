// The metrics query SERVICE spec (P5 task 5.2 · D1 pack `metrics-fe-service`).
//
// The load-bearing pin is P5-M8: the auth middleware 302-redirects a
// request whose `Accept` is the browser's wildcard (`*/*`), so every
// door call MUST carry the explicit JSON accept header. Dropping
// `JSON_HEADERS` from a call turns this spec RED.

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { readFileSync } from 'fs';
import { join } from 'path';

import { MetricsService } from './metrics.service';
import {
  MetricsFamiliesPayload,
  MetricsSeriesPayload,
  MetricsSnapshotPayload,
} from '../models/metrics.model';

const serviceSource = readFileSync(join(__dirname, 'metrics.service.ts'), 'utf-8');

function envelope() {
  return {
    schema_version: 1,
    vocabulary_version: 2,
    generated_at_utc: '2026-11-02T09:00:00+00:00',
    restart_epoch: 'abc123',
    activation: {
      listener_enabled: false,
      census_enabled: true,
      query_api: 'auth-gated-always-on',
    },
  };
}

describe('MetricsService — the URL constants', () => {
  let service: MetricsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(MetricsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('declares the three door routes as RELATIVE /api/... paths', () => {
    expect(MetricsService.FAMILIES_URL).toBe('/api/metrics/families');
    expect(MetricsService.SNAPSHOT_URL).toBe('/api/metrics/snapshot');
    expect(MetricsService.SERIES_URL).toBe('/api/metrics/series');
  });

  it('declares the explicit JSON accept header (P5-M8)', () => {
    expect(MetricsService.JSON_HEADERS).toEqual({ Accept: 'application/json' });
  });

  it('sends the JSON accept header on the families call (P5-M8)', () => {
    service.getFamilies().subscribe();
    const req = http.expectOne(MetricsService.FAMILIES_URL);
    expect(req.request.headers.get('Accept')).toBe('application/json');
    req.flush({ ...envelope(), families: [], truncated: false } as MetricsFamiliesPayload);
  });

  it('sends the JSON accept header on the snapshot call (P5-M8)', () => {
    service.getSnapshot().subscribe();
    const req = http.expectOne(MetricsService.SNAPSHOT_URL);
    expect(req.request.headers.get('Accept')).toBe('application/json');
    req.flush({ ...envelope(), series: [], truncated: false } as MetricsSnapshotPayload);
  });

  it('sends the JSON accept header on the series call (P5-M8)', () => {
    service.getSeries({ names: ['ensemble_loop_lag_seconds'], windowS: 3600 }).subscribe();
    const req = http.expectOne((candidate) => candidate.url === MetricsService.SERIES_URL);
    expect(req.request.headers.get('Accept')).toBe('application/json');
    req.flush({
      ...envelope(),
      window: {
        from_utc: '2026-11-02T08:00:00+00:00',
        to_utc: '2026-11-02T09:00:00+00:00',
        interval_s: 15,
        interval_s_source: 'observed',
      },
      last_sample_age_s: 1,
      stale: false,
      series: [],
      truncated: false,
    } as MetricsSeriesPayload);
  });

  it('requests the series window and the name selectors', () => {
    service
      .getSeries({ names: ['a_metric', 'b_metric'], windowS: 3600, maxPoints: 240 })
      .subscribe();
    const req = http.expectOne((candidate) => candidate.url === MetricsService.SERIES_URL);
    expect(req.request.params.getAll('names')).toEqual(['a_metric', 'b_metric']);
    expect(req.request.params.get('window_s')).toBe('3600');
    expect(req.request.params.get('max_points')).toBe('240');
    req.flush({
      ...envelope(),
      window: {
        from_utc: '2026-11-02T08:00:00+00:00',
        to_utc: '2026-11-02T09:00:00+00:00',
        interval_s: 15,
        interval_s_source: 'observed',
      },
      last_sample_age_s: 1,
      stale: false,
      series: [],
      truncated: false,
    } as MetricsSeriesPayload);
  });

  it('omits the series filters entirely when none are given', () => {
    service.getSeries().subscribe();
    const req = http.expectOne((candidate) => candidate.url === MetricsService.SERIES_URL);
    expect(req.request.params.keys()).toEqual([]);
    req.flush({
      ...envelope(),
      window: {
        from_utc: '2026-11-02T08:00:00+00:00',
        to_utc: '2026-11-02T09:00:00+00:00',
        interval_s: 15,
        interval_s_source: 'observed',
      },
      last_sample_age_s: 1,
      stale: false,
      series: [],
      truncated: false,
    } as MetricsSeriesPayload);
  });
});

describe('MetricsService — the surface fences', () => {
  it('never addresses the exposition LISTENER surface (:9798)', () => {
    // The FE polls the QUERY door only — the listener is the
    // unauthenticated Prometheus surface and is never a FE target.
    expect(serviceSource).not.toContain('9798');
    expect(serviceSource).not.toContain('metrics_listener');
  });

  it('uses no environment file and no absolute URL', () => {
    expect(serviceSource).not.toMatch(/from\s+'[^']*environments/);
    expect(serviceSource).not.toContain('http://');
    expect(serviceSource).not.toContain('https://');
  });
});
