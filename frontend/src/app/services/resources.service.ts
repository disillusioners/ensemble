import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  AnomaliesPayload,
  EvidencePayload,
  HistoryPayload,
  LivePayload,
  ResourcesResolution,
  ResourcesWindow,
  SelfCostPayload,
  TracebackPayload,
} from '../models/resources.models';

/**
 * Typed client for the resource-observability surface
 * (`/api/resources/*`, frozen §2.8 payloads).
 *
 * Same-origin, no auth plumbing (D1 — the app is unauthenticated
 * today). The daemon degrades to empty shapes rather than 503, so the
 * page's error branch is a genuine-unreachability path, not a
 * feature-availability probe.
 *
 * URL constants are the single source of truth for the FE ↔ BE
 * surface; the six methods are exactly the six GETs the FE consumes
 * (§4). `control-capabilities` / `profile` belong to P6 and are
 * deliberately absent.
 */
@Injectable({
  providedIn: 'root',
})
export class ResourcesService {
  private readonly http = inject(HttpClient);

  static readonly LIVE_URL = '/api/resources/live';
  static readonly HISTORY_URL = '/api/resources/history';
  static readonly ANOMALIES_URL = '/api/resources/anomalies';
  static readonly TRACEBACK_URL = '/api/resources/traceback';
  static readonly SELF_COST_URL = '/api/resources/self-cost';

  /** `GET /api/resources/live` — live tab snapshot. */
  getLive(): Observable<LivePayload> {
    return this.http.get<LivePayload>(ResourcesService.LIVE_URL);
  }

  /**
   * `GET /api/resources/history?window=&resolution=` — timeline points.
   * `resolution` defaults to `auto` (server picks raw/minute/hour).
   */
  getHistory(
    window: ResourcesWindow,
    resolution: ResourcesResolution = 'auto',
  ): Observable<HistoryPayload> {
    const params = new HttpParams()
      .set('window', window)
      .set('resolution', resolution);
    return this.http.get<HistoryPayload>(ResourcesService.HISTORY_URL, { params });
  }

  /** `GET /api/resources/anomalies?window=&limit=` — timeline markers. */
  getAnomalies(window?: ResourcesWindow, limit?: number): Observable<AnomaliesPayload> {
    let params = new HttpParams();
    if (window !== undefined) {
      params = params.set('window', window);
    }
    if (limit !== undefined) {
      params = params.set('limit', String(limit));
    }
    return this.http.get<AnomaliesPayload>(ResourcesService.ANOMALIES_URL, { params });
  }

  /** `GET /api/resources/anomalies/{id}/evidence` — drill-down detail. */
  getEvidence(id: string): Observable<EvidencePayload> {
    return this.http.get<EvidencePayload>(
      `${ResourcesService.ANOMALIES_URL}/${encodeURIComponent(id)}/evidence`,
    );
  }

  /** `GET /api/resources/traceback?t0=&t1=` — joined window view. */
  getTraceback(t0: string, t1: string): Observable<TracebackPayload> {
    const params = new HttpParams().set('t0', t0).set('t1', t1);
    return this.http.get<TracebackPayload>(ResourcesService.TRACEBACK_URL, { params });
  }

  /** `GET /api/resources/self-cost` — the monitor's own footprint. */
  getSelfCost(): Observable<SelfCostPayload> {
    return this.http.get<SelfCostPayload>(ResourcesService.SELF_COST_URL);
  }
}
