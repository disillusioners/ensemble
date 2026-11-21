import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, TimeoutError, catchError, throwError, timeout } from 'rxjs';

import { MetricsDoorTimeoutError } from '../models/metrics.model';
import {
  ToolUsageDailySeries,
  ToolUsageSummary,
} from '../models/tool-usage.models';

/**
 * Typed client for the TOOL-USAGE DOMAIN route (the
 * tool-usage-metrics-p1 lane, phase 4 — `daemon/routers/tool_usage.py`,
 * owned by Phase 1; this client CONSUMES the frozen contract).
 *
 * ONE door, TWO reads (phase1-plan §3.4):
 *
 *   GET /api/tool-usage/summary   — the per-tool ledger totals
 *   GET /api/tool-usage/daily     — one tool's per-UTC-day series
 *
 * The client mirrors the house shape of `NestReportsService` (the
 * domain-route precedent): RELATIVE `/api/...` paths (same-origin;
 * there is no `src/environments/` in this app), the explicit JSON
 * accept header (the auth middleware 302-redirects a wildcard-Accept
 * request to an HTML login page), and the ~15 s client deadline.
 *
 * The SUPERSEDE discipline is the CALLER's (the frozen contract, Task
 * 3): the tab body owns one monotonic token per request CLASS — the
 * summary and the daily series are separate classes. This service
 * carries NO internal token of its own (pinned by
 * `tool-usage.service.spec.ts`).
 */
@Injectable({
  providedIn: 'root',
})
export class ToolUsageService {
  private readonly http = inject(HttpClient);

  static readonly SUMMARY_URL = '/api/tool-usage/summary';

  static readonly DAILY_URL = '/api/tool-usage/daily';

  /**
   * The explicit JSON accept header — NOT decoration: the auth
   * middleware 302-redirects a wildcard-Accept request to an HTML
   * login page (the `MetricsService` precedent, `P5-M8`).
   */
  static readonly JSON_HEADERS: { readonly Accept: string } = { Accept: 'application/json' };

  /** The client-side deadline — the house door budget (15 s). */
  static readonly DOOR_TIMEOUT_MS = 15_000;

  /**
   * The house timeout pattern (`MetricsService.withDoorTimeout`): the
   * rxjs `timeout` operator + the typed failure the decoder names.
   */
  private withDeadline<T>(source: Observable<T>): Observable<T> {
    return source.pipe(
      timeout(ToolUsageService.DOOR_TIMEOUT_MS),
      catchError((err: unknown) => {
        if (err instanceof TimeoutError) {
          return throwError(() => new MetricsDoorTimeoutError());
        }
        return throwError(() => err);
      }),
    );
  }

  /**
   * The query params — forwarded ONLY when set (the frozen contract:
   * `project_id` OMITTED = all projects INCLUDING the `__none__`
   * bucket; `days` defaults to the BE's own default when absent). An
   * `undefined` value is NOT forwarded.
   */
  private params(source: Record<string, string | number | undefined>): HttpParams {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(source)) {
      if (value !== undefined) {
        params = params.set(key, String(value));
      }
    }
    return params;
  }

  /**
   * `GET /api/tool-usage/summary` — the per-tool ledger totals, UNSORTED
   * (the FE sorts by `calls_7d` DESC client-side).
   */
  summary(options: { projectId?: string; days?: number } = {}): Observable<ToolUsageSummary> {
    const params = this.params({
      project_id: options.projectId,
      days: options.days,
    });
    return this.withDeadline(
      this.http.get<ToolUsageSummary>(ToolUsageService.SUMMARY_URL, {
        params,
        headers: ToolUsageService.JSON_HEADERS,
      }),
    );
  }

  /**
   * `GET /api/tool-usage/daily` — one tool's per-UTC-day series
   * (zero-filled). `toolName` is REQUIRED by the frozen contract; an
   * unknown tool answers `404 unknown_tool`.
   */
  daily(options: { projectId?: string; toolName: string; days?: number }): Observable<ToolUsageDailySeries> {
    const params = this.params({
      project_id: options.projectId,
      tool_name: options.toolName,
      days: options.days,
    });
    return this.withDeadline(
      this.http.get<ToolUsageDailySeries>(ToolUsageService.DAILY_URL, {
        params,
        headers: ToolUsageService.JSON_HEADERS,
      }),
    );
  }
}
