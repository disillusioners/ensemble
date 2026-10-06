import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError } from 'rxjs';
import {
  SnapshotFilters,
  SnapshotListResponse,
  SnapshotDetailResponse,
  SnapshotUsageMetrics,
} from '../models/snapshot.model';

/**
 * Service for the `/api/snapshots` read surface (snapshot-uiux v1).
 *
 * Owns:
 *
 * * `list(filters)`     — `GET /api/snapshots` with the full filter
 *                         set (project / agent / status / tags / age /
 *                         sort / limit / offset). Envelope key is
 *                         `items` (D-1).
 * * `getById(id, opts)` — `GET /api/snapshots/{id}` with optional
 *                         `?include=digest` (OK-4 — lazy digest).
 * * `getMetrics()`      — `GET /api/snapshots/metrics` (R16 surface
 *                         — capture + spawn counts; see also the
 *                         legacy `GET /api/settings/snapshot-usage-metrics`
 *                         1-line re-export documented in §3.6
 *                         fallback of fe-plan).
 * * `buildParams(f)`    — pure helper that maps the `SnapshotFilters`
 *                         shape to an `HttpParams` instance, with the
 *                         D-2 wire-param rename `agent_id → agent`.
 *                         Multi-value `status` and `tags` use the
 *                         repeat-param idiom (OK-2).
 * * `computeAgeCutoff(p)` — pure helper that returns the ISO-8601
 *                          `created_after` cutoff for the chosen age
 *                          preset, or `null` for `all` (D-7).
 *
 * URL constants are the single source of truth for the FE↔BE surface.
 * If the BE plan keeps `getSnapshotUsageMetrics` at the legacy path
 * (fe-plan §3.6), change `METRICS_URL` only.
 *
 * State management follows the project pattern: `signal()` for cached
 * reads, `Observable` for the HTTP surface. The page host owns the
 * list/loading/error state (pass 4 amendment #8); this service does
 * NOT hold per-call list state — only the metrics cache (so the
 * metrics strip can render without re-fetching on tab-toggle).
 */
@Injectable({
  providedIn: 'root',
})
export class SnapshotService {
  private readonly http = inject(HttpClient);

  // URL constants (single source for the FE ↔ BE surface)
  static readonly LIST_URL = '/api/snapshots';
  static readonly DETAIL_URL = '/api/snapshots'; // + '/{id}' at call
  static readonly METRICS_URL = '/api/snapshots/metrics';

  // ── Cached signals ───────────────────────────────────────────────
  // `metrics` is the only list-state-style cache the service holds;
  // the page owns `records` / `total` (pass 4 amendment #8).
  readonly metrics = signal<SnapshotUsageMetrics | null>(null);

  // ── HTTP methods (return Observables) ─────────────────────────────

  /**
   * GET /api/snapshots with the full filter set.
   *
   * Pure Observable — does NOT mutate the page's records/total
   * signals (the page host owns those per pass 4 amendment #8).
   * Errors propagate to the caller (caller renders its own error
   * state + retry path); the service does not swallow.
   */
  list(filters: SnapshotFilters): Observable<SnapshotListResponse> {
    const params = this.buildParams(filters);
    return this.http.get<SnapshotListResponse>(SnapshotService.LIST_URL, {
      params,
    });
  }

  /**
   * GET /api/snapshots/{id}.
   *
   * `opts.includeDigest === true` appends `?include=digest` so the
   * BE populates the `digest` field (OK-4). Default (`false`) sends
   * no query string — the detail returns the full row + `task_summary`
   * but `digest: {}`.
   */
  getById(
    id: string,
    opts: { includeDigest: boolean },
  ): Observable<SnapshotDetailResponse> {
    let params = new HttpParams();
    if (opts.includeDigest) {
      params = params.set('include', 'digest');
    }
    return this.http.get<SnapshotDetailResponse>(
      `${SnapshotService.DETAIL_URL}/${encodeURIComponent(id)}`,
      { params },
    );
  }

  /**
   * GET /api/snapshots/metrics — R16 read surface (capture + spawn
   * counts). On success the result is cached into the `metrics`
   * signal so the page can re-render without re-fetching.
   */
  getMetrics(): Observable<SnapshotUsageMetrics> {
    return this.http
      .get<SnapshotUsageMetrics>(SnapshotService.METRICS_URL)
      .pipe(
        tap((m) => this.metrics.set(m)),
        catchError((err) => {
          this.metrics.set(null);
          // Re-throw so the caller can render its own error UI
          // (fe-plan §7.4: "Inline small retry button in the metrics
          // area — NOT a page-level error — the page is usable
          // without metrics").
          throw err;
        }),
      );
  }

  // ── Pure helpers (testable in isolation) ─────────────────────────

  /**
   * Encode a `SnapshotFilters` instance as `HttpParams`.
   *
   * Wire-contract notes:
   *
   * * `filters.agent_id` → wire `agent` (D-2).
   * * `status[]` and `tags[]` are repeated params (OK-2) via
   *   `HttpParams.append(key, value)`.
   * * `tag_mode` is sent as a single param (BE default `all`; FE
   *   only sends when non-default to keep the URL clean).
   * * `age` is FE-only — translated to `created_after` via
   *   `computeAgeCutoff`. When `age === 'all'` (D-7 default), the
   *   `created_after` param is omitted entirely.
   * * `sort` is sent when non-default (BE default is `created_at_desc`).
   * * `limit` and `offset` are ALWAYS sent (D-6) so the paginator's
   *   state is always reflected in the URL.
   */
  buildParams(filters: SnapshotFilters): HttpParams {
    let params = new HttpParams();

    if (filters.project_id) {
      params = params.set('project_id', filters.project_id);
    }
    // D-2: wire name is `agent`, not `agent_id`.
    if (filters.agent_id) {
      params = params.set('agent', filters.agent_id);
    }

    // Status — repeat params (empty array = no param).
    for (const s of filters.status) {
      params = params.append('status', s);
    }

    // Tags — repeat params + tag_mode (sent only when non-default).
    for (const t of filters.tags) {
      params = params.append('tags', t);
    }
    if (filters.tag_mode !== 'all') {
      params = params.set('tag_mode', filters.tag_mode);
    }

    // Age → created_after (omit when `all` — D-7 default).
    const cutoff = this.computeAgeCutoff(filters.age);
    if (cutoff !== null) {
      params = params.set('created_after', cutoff);
    }

    // Sort — send only when non-default to keep URLs clean.
    if (filters.sort !== 'created_at_desc') {
      params = params.set('sort', filters.sort);
    }

    // limit / offset — ALWAYS sent (D-6).
    params = params.set('limit', String(filters.limit));
    params = params.set('offset', String(filters.offset));

    return params;
  }

  /**
   * Return the ISO-8601 `created_after` cutoff for the chosen age
   * preset, or `null` for `all` (which means "no lower bound" — the
   * FE OMITS the `created_after` param entirely per D-7).
   *
   * `24h` → now - 24h
   * `7d`  → now - 7d
   * `30d` → now - 30d
   * `all` → null
   *
   * Optional `now` injection for testability (defaults to the current
   * instant). Returns UTC ISO strings with millisecond precision so
   * the BE's `datetime.fromisoformat` parses cleanly.
   */
  computeAgeCutoff(
    preset: SnapshotFilters['age'],
    now: Date = new Date(),
  ): string | null {
    if (preset === 'all') {
      return null;
    }
    const cutoff = new Date(now.getTime());
    switch (preset) {
      case '24h':
        cutoff.setUTCHours(cutoff.getUTCHours() - 24);
        break;
      case '7d':
        cutoff.setUTCDate(cutoff.getUTCDate() - 7);
        break;
      case '30d':
        cutoff.setUTCDate(cutoff.getUTCDate() - 30);
        break;
    }
    return cutoff.toISOString();
  }
}
