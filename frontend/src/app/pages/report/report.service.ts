import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, EMPTY, catchError, tap } from 'rxjs';
import type { MaintenanceReportRun } from '../../models';

/**
 * HTTP service for the Report page (Central Maintenance System Phase 4,
 * D-E / BR-18).
 *
 * Wires the single READ-ONLY GET endpoint of the maintenance-reports
 * surface to a typed Angular signal:
 *
 *   GET /api/maintenance-reports → loadRuns()
 *
 * **GET ONLY — there is no write lane here.** The report is authored by
 * the daemon's `run-report-writer` worker child; the browser only ever
 * READS the run list, FETCHES a run's `report_view_url` for the
 * in-app render (`fetchViewMarkdown`), and opens the artifact links
 * (live_views `maintenance-reports` root). The service carries no
 * post/put/delete/patch call — pinned by `report.pins.spec.ts`.
 *
 * Auth-off posture (cross-lane contract note): the BE door returns
 * 503 AUTH_NOT_CONFIGURED when auth is off. The service surfaces a
 * friendly "Auth not configured (fail-closed)." message instead of
 * crashing on the error body; the component renders it inline.
 *
 * The URL is centralized in `REPORTS_URL` so a one-line reconcile with
 * the parallel BE lane lands in a single place (the BE contract is
 * pinned to `/api/maintenance-reports`).
 */
@Injectable({ providedIn: 'root' })
export class ReportService {
  private readonly http = inject(HttpClient);

  /** The single read endpoint (centralized for one-line reconcile). */
  static readonly REPORTS_URL = '/api/maintenance-reports';

  /** The run list (empty until the first successful read). */
  readonly runs = signal<MaintenanceReportRun[]>([]);

  /** `true` while a read is in flight. */
  readonly loading = signal(false);

  /** The last read error, or `null` when healthy. */
  readonly error = signal<string | null>(null);

  /** `GET /api/maintenance-reports`. */
  loadRuns(): Observable<MaintenanceReportRun[]> {
    this.loading.set(true);
    this.error.set(null);
    return this.http.get<MaintenanceReportRun[]>(ReportService.REPORTS_URL).pipe(
      tap((rows) => {
        this.runs.set(Array.isArray(rows) ? rows : []);
        this.loading.set(false);
      }),
      catchError((err: HttpErrorResponse) => {
        this.loading.set(false);
        const body = (err?.error ?? {}) as {
          detail?: { error?: string; message?: string };
          message?: string;
          error?: string;
        };
        const code =
          body?.detail?.error ?? body?.error ?? null;
        if (code === 'AUTH_NOT_CONFIGURED' || err?.status === 503) {
          this.error.set('Auth not configured (fail-closed).');
        } else {
          this.error.set(
            body?.detail?.message ??
              body?.message ??
              err?.message ??
              'Failed to load maintenance reports.',
          );
        }
        return EMPTY;
      }),
    );
  }

  /**
   * `GET <viewUrl>` — read a maintenance artifact's VIEW resource.
   *
   * The URL is the SAME relative `/views/maintenance-reports/...` path
   * the run list already carries (the FE never rewrites it — see
   * `models/report.model.ts`). It is a VIEW resource read, NOT an API
   * mutation: GET only, no write verb (pinned by
   * `report.pins.spec.ts`).
   *
   * The response is the daemon's markdown WRAPPER page (HTML): the
   * live-views markdown lane wraps every `.md` artifact
   * (`daemon/routers/live_views.py:2122`) and offers NO raw mode for
   * `.md` (`?raw=1` is the mermaid lane only). The component extracts
   * the raw markdown from the wrapper's stable
   * `<article id="rendered"><pre>` element — see
   * `extractMarkdownFromWrapper` in `report.component.ts`.
   */
  fetchViewMarkdown(viewUrl: string): Observable<string> {
    return this.http.get(viewUrl, { responseType: 'text' });
  }
}
