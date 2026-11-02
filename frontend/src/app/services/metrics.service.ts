import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  MetricsFamiliesPayload,
  MetricsSeriesPayload,
  MetricsSnapshotPayload,
} from '../models/metrics.model';

/**
 * Typed client for the central-metrics query door (P5 task 5.2).
 *
 * ONE door, THREE routes (`docs/architecture/metrics-query-contract.md`
 * §4.7):
 *
 *   GET /api/metrics/families  — the family catalog AS SERVED
 *   GET /api/metrics/snapshot  — the current published values
 *   GET /api/metrics/series    — the windowed trend
 *
 * The URL constants are the single FE↔BE surface truth — the same
 * house pattern the resources page uses (`resources.service.ts`).
 * They are RELATIVE `/api/...` paths: there is no `src/environments/`
 * in this app and the door is always same-origin.
 *
 * `JSON_HEADERS` is NOT optional decoration: the auth middleware
 * 302-redirects a request whose `Accept` is the browser's wildcard
 * (an asterisk plus a slash), which turns a JSON call into an HTML
 * login page. The explicit `Accept: application/json` keeps the door's
 * answer on the JSON path (P5-M8 pins its presence).
 */
@Injectable({
  providedIn: 'root',
})
export class MetricsService {
  private readonly http = inject(HttpClient);

  static readonly FAMILIES_URL = '/api/metrics/families';
  static readonly SNAPSHOT_URL = '/api/metrics/snapshot';
  static readonly SERIES_URL = '/api/metrics/series';

  /**
   * The explicit JSON accept header. The auth middleware 302-redirects
   * a wildcard-Accept request, so every door call carries this.
   */
  static readonly JSON_HEADERS: { readonly Accept: string } = { Accept: 'application/json' };

  /** `GET /api/metrics/families` — the family catalog. */
  getFamilies(): Observable<MetricsFamiliesPayload> {
    return this.http.get<MetricsFamiliesPayload>(MetricsService.FAMILIES_URL, {
      headers: MetricsService.JSON_HEADERS,
    });
  }

  /** `GET /api/metrics/snapshot` — the current published values. */
  getSnapshot(): Observable<MetricsSnapshotPayload> {
    return this.http.get<MetricsSnapshotPayload>(MetricsService.SNAPSHOT_URL, {
      headers: MetricsService.JSON_HEADERS,
    });
  }

  /**
   * `GET /api/metrics/series` — the windowed trend.
   *
   * `names` is the NAME-level selector (every label variant of a name);
   * the door accepts the repeated and the comma-separated form and
   * REJECTS an over-ceiling selector (never silently truncates it).
   * `windowS` is the window in seconds (the Charts tab asks for 3600
   * ⇒ 240 points at the `max_points` default of 240).
   */
  getSeries(
    options: { names?: readonly string[]; windowS?: number; maxPoints?: number } = {},
  ): Observable<MetricsSeriesPayload> {
    let params = new HttpParams();
    for (const name of options.names ?? []) {
      params = params.append('names', name);
    }
    if (options.windowS !== undefined) {
      params = params.set('window_s', String(options.windowS));
    }
    if (options.maxPoints !== undefined) {
      params = params.set('max_points', String(options.maxPoints));
    }
    return this.http.get<MetricsSeriesPayload>(MetricsService.SERIES_URL, {
      params,
      headers: MetricsService.JSON_HEADERS,
    });
  }
}
