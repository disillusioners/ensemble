import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpErrorResponse } from '@angular/common/http';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MarkdownModule } from 'ngx-markdown';
import { Subscription } from 'rxjs';
import { ReportService } from './report.service';
import type { MaintenanceReportRun } from '../../models';

/**
 * The detail-pane message for an ABSENT artifact — the daemon's
 * uniform 404 (`daemon/routers/live_views.py:749`) fires when a
 * partial run has no report half on disk.
 */
const REPORT_ABSENT = 'Report not available (this run has no report artifact).';

/** The detail-pane message for any other fetch / parse failure. */
const REPORT_UNAVAILABLE = 'Failed to load the report.';

/**
 * Extract the raw markdown from the daemon's markdown WRAPPER page.
 *
 * WHY A WRAPPER (the response-shape investigation — read-only,
 * daemon-side evidence):
 *
 * * `daemon/routers/live_views.py:2122` — the `.md` branch of the
 *   live-views lane calls `_render_markdown_response(...)`, which
 *   answers `text/html; charset=utf-8` (the WRAPPER page), never the
 *   raw markdown.
 * * There is NO raw escape hatch for `.md`: `?raw=1` is the MERMAID
 *   lane only — `_wants_raw_query` is consulted at
 *   `daemon/routers/live_views.py:2076`, `:3330` and `:3480`, and
 *   every one of those call sites is guarded by
 *   `is_mermaid_extension(...)`. There is no Accept-header
 *   negotiation on the success path either (the Accept-aware logic
 *   in that module shapes the ERROR envelopes only — `_uniform_404`
 *   at `daemon/routers/live_views.py:749`).
 * * BOTH wrapper templates embed the raw markdown, HTML-escaped, in
 *   the SAME stable element: `<article id="rendered"><pre>…</pre></
 *   article>` — `daemon/services/live_views.py:1843` (plain wrapper)
 *   and `daemon/services/live_views.py:2364` (mermaid-aware
 *   wrapper).
 * * The daemon's own bootstrap reads that element's `textContent`
 *   (`daemon/services/live_views.py:1850`) and runs marked +
 *   DOMPurify over it — i.e. the element IS the raw-markdown carrier
 *   by the daemon's own contract.
 *
 * So the extraction parses OUR OWN daemon's stable wrapper format —
 * deterministic, zero daemon changes. `DOMParser` does NOT execute
 * scripts, so the fetched wrapper's bootstrap never runs and the
 * `<pre>` still carries the raw source; `textContent` decodes the
 * entities `html.escape` wrote, yielding the original markdown.
 *
 * Returns `null` when the payload carries no markdown element — the
 * caller renders the "not available" posture (fail-closed, never a
 * blank pane).
 */
export function extractMarkdownFromWrapper(wrapperHtml: string): string | null {
  const doc = new DOMParser().parseFromString(wrapperHtml, 'text/html');
  const pre = doc.querySelector('article#rendered pre');
  return pre ? pre.textContent : null;
}

/**
 * Report — the READ-ONLY operator page for the Central Maintenance
 * System (Phase 4, D-E / BR-18).
 *
 * Reads the run list from `GET /api/maintenance-reports` and renders a
 * selected run's report IN-APP: the run's `report_view_url` is fetched
 * (a same-origin GET of the live_views `maintenance-reports` root)
 * and the markdown it carries is rendered with the house markdown
 * precedent — `ngx-markdown`'s `<markdown [data]>`, the same binding
 * the Blueprint page uses for daemon-served markdown content
 * (`frontend/src/app/pages/blueprint/blueprint.component.html:524`).
 *
 * The page never writes: the report is authored by the daemon's
 * `run-report-writer` worker child.
 *
 * NO EMBEDDED VIEW — the report is rendered IN the page, not in a
 * child browsing context. The daemon's markdown wrapper sets
 * `frame-ancestors 'none'` (`daemon/services/live_views.py:1798`),
 * so an embedded view was never going to render; fetching the
 * artifact and rendering its markdown here removes the whole problem
 * class (no embedded document, no sanitizer bypass, no CSP change).
 *
 * GEAR MENU ONLY — there is NO top-level header link (the header nav
 * carries the primary destinations only). Lazy route registered before
 * the catch-all wildcard in `app.routes.ts`.
 */
@Component({
  selector: 'app-report',
  standalone: true,
  imports: [
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MarkdownModule,
  ],
  templateUrl: './report.component.html',
  styleUrl: './report.component.scss',
})
export class ReportComponent {
  readonly service = inject(ReportService);

  readonly runs = this.service.runs;
  readonly loading = this.service.loading;
  readonly error = this.service.error;

  /** The run whose report is currently rendered, or `null`. */
  readonly selected = signal<MaintenanceReportRun | null>(null);

  /**
   * The raw markdown of the selected run's report — rendered in-app.
   * Empty until a selection resolves; cleared on close / re-select.
   */
  readonly reportMarkdown = signal<string>('');

  /** `true` while the selected run's report is being fetched. */
  readonly reportLoading = signal(false);

  /**
   * The DETAIL-pane error — the fetch failed, the artifact is absent
   * (the daemon's uniform 404 when a partial run has no report half),
   * or the response carried no markdown payload.
   *
   * Deliberately NOT the service's list-level `error`: a missing
   * artifact must not disturb the run list — the list stays usable.
   */
  readonly reportError = signal<string | null>(null);

  private readonly destroyRef = inject(DestroyRef);

  /** The in-flight report fetch (cancelled on re-select / close). */
  private reportRequest?: Subscription;

  constructor() {
    this.service.loadRuns().subscribe();
  }

  select(run: MaintenanceReportRun): void {
    this.selected.set(run);
    this.loadReport(run);
  }

  clearSelection(): void {
    this.cancelReportRequest();
    this.selected.set(null);
    this.reportMarkdown.set('');
    this.reportError.set(null);
    this.reportLoading.set(false);
  }

  trackByRunId(_index: number, run: MaintenanceReportRun): string {
    return run.run_id;
  }

  /**
   * Fetch the selected run's report artifact and render it in-app.
   *
   * A new selection CANCELS the in-flight request: a late response
   * for the previously-selected run can never overwrite the current
   * one.
   */
  private loadReport(run: MaintenanceReportRun): void {
    this.cancelReportRequest();
    this.reportMarkdown.set('');
    this.reportError.set(null);
    this.reportLoading.set(true);

    this.reportRequest = this.service
      .fetchViewMarkdown(run.report_view_url)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (body) => {
          const markdown = extractMarkdownFromWrapper(body);
          if (markdown === null) {
            this.reportMarkdown.set('');
            this.reportError.set(REPORT_UNAVAILABLE);
          } else {
            this.reportMarkdown.set(markdown);
          }
          this.reportLoading.set(false);
        },
        error: (err: HttpErrorResponse) => {
          this.reportMarkdown.set('');
          this.reportError.set(
            err?.status === 404 ? REPORT_ABSENT : REPORT_UNAVAILABLE,
          );
          this.reportLoading.set(false);
        },
      });
  }

  private cancelReportRequest(): void {
    this.reportRequest?.unsubscribe();
    this.reportRequest = undefined;
  }
}
