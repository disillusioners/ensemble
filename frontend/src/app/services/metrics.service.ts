import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, TimeoutError, catchError, throwError, timeout } from 'rxjs';
import {
  MetricsDoorTimeoutError,
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

  /**
   * The CLIENT-SIDE door deadline (T1 phase 2, task 2.10 — AM-4).
   *
   * The door has NO server-side read deadline of its own, so a hung
   * read left the page in the loading state with NO state at all — the
   * worst possible outcome for an operator ("is it broken, or is it
   * empty?"). The client therefore gives up after ~15 s and reports the
   * `timeout` class.
   *
   * NO Retry: the read may still be in flight server-side, so an
   * automatic retry would pile a second request onto a door that is
   * already slow.
   */
  static readonly DOOR_TIMEOUT_MS = 15_000;

  /**
   * The house timeout pattern (`api.service.ts:366-367`): the rxjs
   * `timeout` operator + a typed failure. The rxjs `TimeoutError` is
   * mapped to the typed `MetricsDoorTimeoutError` so the decoder can
   * name the class; every other failure is rethrown UNCHANGED.
   */
  private withDoorTimeout<T>(source: Observable<T>): Observable<T> {
    return source.pipe(
      timeout(MetricsService.DOOR_TIMEOUT_MS),
      catchError((err: unknown) => {
        if (err instanceof TimeoutError) {
          return throwError(() => new MetricsDoorTimeoutError());
        }
        return throwError(() => err);
      }),
    );
  }

  /** `GET /api/metrics/families` — the family catalog. */
  getFamilies(): Observable<MetricsFamiliesPayload> {
    return this.withDoorTimeout(
      this.http.get<MetricsFamiliesPayload>(MetricsService.FAMILIES_URL, {
        headers: MetricsService.JSON_HEADERS,
      }),
    );
  }

  /**
   * `GET /api/metrics/snapshot` — the current published values.
   *
   * `family` narrows the page to ONE family (the door accepts it —
   * `daemon/routers/metrics.py:661-668`); the per-tab stat strip uses
   * it to ask for exactly the families it renders.
   */
  getSnapshot(options: { family?: string } = {}): Observable<MetricsSnapshotPayload> {
    let params = new HttpParams();
    if (options.family) {
      params = params.set('family', options.family);
    }
    return this.withDoorTimeout(
      this.http.get<MetricsSnapshotPayload>(MetricsService.SNAPSHOT_URL, {
        params,
        headers: MetricsService.JSON_HEADERS,
      }),
    );
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
    return this.withDoorTimeout(
      this.http.get<MetricsSeriesPayload>(MetricsService.SERIES_URL, {
        params,
        headers: MetricsService.JSON_HEADERS,
      }),
    );
  }
}
