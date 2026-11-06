import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, TimeoutError, catchError, throwError, timeout } from 'rxjs';

import { MetricsDoorTimeoutError } from '../models/metrics.model';
import {
  AnomalyClusterDetailPayload,
  AnomalyClustersPayload,
} from '../models/nest-reports.models';

/**
 * Typed client for the NEST-REPORTS DOMAIN route (the nest-anomaly
 * lane, phase 4 — `daemon/routers/nest_reports.py`).
 *
 * ONE door, TWO reads (the same shapes the `nest-report-read` tools
 * consume — one projector, two transports):
 *
 *   GET /api/nest-reports/clusters                 — the grouped incidents
 *   GET /api/nest-reports/clusters/{cluster_id}    — one cluster's members
 *
 * The client mirrors the house shape of `MetricsService` (the metrics
 * door's client): RELATIVE `/api/...` paths (same-origin; there is no
 * `src/environments/` in this app), the explicit JSON accept header,
 * and the ~15 s client deadline. The SUPERSEDE discipline is the
 * caller's (the tab body owns one monotonic token per request CLASS —
 * the list and the detail are separate classes).
 */
@Injectable({
  providedIn: 'root',
})
export class NestReportsService {
  private readonly http = inject(HttpClient);

  static readonly CLUSTERS_URL = '/api/nest-reports/clusters';

  /**
   * The explicit JSON accept header — NOT decoration: the auth
   * middleware 302-redirects a wildcard-Accept request to an HTML
   * login page (the `MetricsService` precedent, `P5-M8`).
   */
  static readonly JSON_HEADERS: { readonly Accept: string } = { Accept: 'application/json' };

  /** The client-side deadline — the house door budget (15 s). */
  static readonly DOOR_TIMEOUT_MS = 15_000;

  /**
   * The detail URL — the id is `anomaly_clusters.id` (NOT the
   * `cluster_key_hash`); `encodeURIComponent` is applied anyway (the
   * house URL-hygiene habit).
   */
  clusterDetailUrl(clusterId: string): string {
    return `${NestReportsService.CLUSTERS_URL}/${encodeURIComponent(clusterId)}`;
  }

  /**
   * The house timeout pattern (`MetricsService.withDoorTimeout`): the
   * rxjs `timeout` operator + the typed failure the decoder names.
   */
  private withDeadline<T>(source: Observable<T>): Observable<T> {
    return source.pipe(
      timeout(NestReportsService.DOOR_TIMEOUT_MS),
      catchError((err: unknown) => {
        if (err instanceof TimeoutError) {
          return throwError(() => new MetricsDoorTimeoutError());
        }
        return throwError(() => err);
      }),
    );
  }

  /**
   * The query params — forwarded ONLY when set (the route defaults to
   * `window_s=3600`, `limit=20`). A `undefined` value is NOT
   * forwarded; `include_excerpt` is therefore absent unless the
   * operator asked for it (AM-12b).
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
   * `GET /api/nest-reports/clusters` — the grouped incident list.
   *
   * `cursor` resumes one bounded page — it is the route's opaque token,
   * passed through verbatim.
   */
  getClusters(
    options: {
      windowS?: number;
      severity?: string;
      category?: string;
      limit?: number;
      cursor?: string;
    } = {},
  ): Observable<AnomalyClustersPayload> {
    const params = this.params({
      window_s: options.windowS,
      severity: options.severity,
      category: options.category,
      limit: options.limit,
      cursor: options.cursor,
    });
    return this.withDeadline(
      this.http.get<AnomalyClustersPayload>(NestReportsService.CLUSTERS_URL, {
        params,
        headers: NestReportsService.JSON_HEADERS,
      }),
    );
  }

  /**
   * `GET /api/nest-reports/clusters/{cluster_id}` — one cluster's
   * member reports, FRESHEST-FIRST.
   *
   * AM-12(b): `includeExcerpt` is forwarded ONLY when the operator
   * asks — the default detail payload carries NO excerpt key anywhere,
   * and the FE renders none.
   */
  getClusterDetail(
    clusterId: string,
    options: { limit?: number; includeExcerpt?: boolean } = {},
  ): Observable<AnomalyClusterDetailPayload> {
    const params = this.params({
      limit: options.limit,
      include_excerpt: options.includeExcerpt === true ? 'true' : undefined,
    });
    return this.withDeadline(
      this.http.get<AnomalyClusterDetailPayload>(this.clusterDetailUrl(clusterId), {
        params,
        headers: NestReportsService.JSON_HEADERS,
      }),
    );
  }
}
