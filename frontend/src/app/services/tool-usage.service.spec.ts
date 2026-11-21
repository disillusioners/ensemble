// The TOOL-USAGE service spec (phase4-plan Task 6).
//
// The load-bearing pins:
//
//   * the URL shapes — the frozen `/api/tool-usage/summary` and
//     `/api/tool-usage/daily` (phase1 §3.4);
//   * the explicit `Accept: application/json` header (the auth
//     middleware 302-redirects a wildcard-Accept request);
//   * the ~15 s client deadline, mapped to the TYPED timeout failure;
//   * `params` forwards ONLY set values — `project_id` omitted = the
//     all-projects semantics, `days` absent = the BE's own default;
//   * the SUPERSEDE token is the CALLER's (the frozen contract): this
//     service carries NO internal token of its own — the behavioral
//     stale-discard leg lives in the tab pack, where the tokens live;
//   * `tool_name` is REQUIRED on the daily read (the frozen contract).

import { readFileSync } from 'fs';
import { join } from 'path';

import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import {
  HttpClient,
  provideHttpClient,
  withInterceptorsFromDi,
} from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';

import { MetricsDoorTimeoutError } from '../models/metrics.model';
import { ToolUsageService } from './tool-usage.service';

describe('ToolUsageService — the frozen contract', () => {
  let service: ToolUsageService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()],
    });
    service = TestBed.inject(ToolUsageService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('declares the FROZEN route URLs', () => {
    expect(ToolUsageService.SUMMARY_URL).toBe('/api/tool-usage/summary');
    expect(ToolUsageService.DAILY_URL).toBe('/api/tool-usage/daily');
  });

  it('declares the explicit JSON accept header + the 15 s deadline (named constants)', () => {
    expect(ToolUsageService.JSON_HEADERS).toEqual({ Accept: 'application/json' });
    expect(ToolUsageService.DOOR_TIMEOUT_MS).toBe(15_000);
  });

  it('the summary read hits /api/tool-usage/summary with Accept: application/json', () => {
    service.summary({ days: 7 }).subscribe();
    const req = http.expectOne('/api/tool-usage/summary?days=7');
    expect(req.request.headers.get('Accept')).toBe('application/json');
    req.flush({ generated_at: '2026-11-21T00:00:00+00:00', rows: [] });
  });

  it('the daily read hits /api/tool-usage/daily with the required tool_name', () => {
    service.daily({ toolName: 'read_file', days: 7 }).subscribe();
    const req = http.expectOne('/api/tool-usage/daily?tool_name=read_file&days=7');
    expect(req.request.headers.get('Accept')).toBe('application/json');
    req.flush({ series: [] });
  });

  it('project_id OMITTED = all-projects (the param is never sent)', () => {
    service.summary({ days: 7 }).subscribe();
    const req = http.expectOne((candidate) =>
      candidate.url.startsWith(ToolUsageService.SUMMARY_URL),
    );
    expect(req.request.params.get('project_id')).toBeNull();
    req.flush({ generated_at: 'x', rows: [] });
  });

  it('project_id SET is forwarded; days absent is NOT forwarded (the BE default applies)', () => {
    service.summary({ projectId: 'proj-9' }).subscribe();
    const req = http.expectOne((candidate) =>
      candidate.url.startsWith(ToolUsageService.SUMMARY_URL),
    );
    expect(req.request.params.get('project_id')).toBe('proj-9');
    expect(req.request.params.get('days')).toBeNull();
    req.flush({ generated_at: 'x', rows: [] });
    http.verify();
  });

  it('the daily read forwards project_id only when set (the tool_name is always required)', () => {
    service.daily({ toolName: 'bash', projectId: 'proj-1', days: 1 }).subscribe();
    const req = http.expectOne((candidate) => candidate.url.startsWith(ToolUsageService.DAILY_URL));
    expect(req.request.params.get('tool_name')).toBe('bash');
    expect(req.request.params.get('project_id')).toBe('proj-1');
    expect(req.request.params.get('days')).toBe('1');
    req.flush({ series: [] });
    http.verify();
  });

  it('maps the expiry to the TYPED timeout failure (never network)', fakeAsync(() => {
    let captured: unknown = null;
    service.summary({ days: 7 }).subscribe({
      error: (err: unknown) => {
        captured = err;
      },
    });
    const req = http.expectOne((candidate) =>
      candidate.url.startsWith(ToolUsageService.SUMMARY_URL),
    );
    // The clock test — NOT a real 15 s wait.
    tick(ToolUsageService.DOOR_TIMEOUT_MS + 1);
    expect(captured).toBeInstanceOf(MetricsDoorTimeoutError);
    // The expiry UNSUBSCRIBES the source — the in-flight request is
    // cancelled by the deadline itself.
    expect(req.cancelled).toBe(true);
  }));

  it('carries NO internal supersede token — the token is the CALLER\u2019s (the frozen contract)', () => {
    // The service is a dumb pipe: two subscriptions of the SAME read are
    // two independent requests, and neither answer can clobber the other
    // — the caller's monotonic token decides what lands (the behavioral
    // stale-discard leg lives in the tab pack).
    const answers: number[] = [];
    service.summary({ days: 7 }).subscribe((payload) => {
      answers.push((payload as unknown as { rows: unknown[] }).rows.length);
    });
    service.summary({ days: 7 }).subscribe((payload) => {
      answers.push((payload as unknown as { rows: unknown[] }).rows.length);
    });
    const reqs = http.match((candidate) =>
      candidate.url.startsWith(ToolUsageService.SUMMARY_URL),
    );
    expect(reqs.length).toBe(2);
    reqs[0].flush({ generated_at: 'a', rows: [1, 2] });
    reqs[1].flush({ generated_at: 'b', rows: [] });
    // The two answers arrive independently — the FIRST caller got the
    // FIRST answer, byte-for-byte.
    expect(answers).toEqual([2, 0]);
    http.verify();
  });

  it('the service source imports no chart vendor and no token counter (the hermetic posture)', () => {
    // Mirrors the bundle-guard's lane hygiene: the service is a plain
    // typed client — no chart library, no internal monotonic token (the
    // caller owns the supersede discipline).
    const source = readFileSync(join(__dirname, 'tool-usage.service.ts'), 'utf-8');
    expect(source).not.toContain('chart.js');
    expect(source).not.toMatch(/private\s+\w*(seq|Seq|token|Token)\s*[=:]/);
  });

  it('the service works over the DI HttpClient (a plain typed client, nothing exotic)', () => {
    // A construction probe — the injectable resolves from the DI tree.
    expect(TestBed.inject(HttpClient)).toBeTruthy();
  });
});
