import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, throwError, finalize } from 'rxjs';
import { Work, WorkFilters } from '../models/work.model';

/**
 * Service for the unified ``GET /api/work`` read API
 * (Virtual Job Management Surface, Phase 4).
 *
 * Mirrors the constructor-injection + signals pattern used by
 * ``QueueService`` and ``JobService`` so the Jobs page can wire it
 * alongside them without surprises.
 *
 * The service exposes a single read method (``getWork``) — the
 * surface is intentionally read-only here. Mutations on individual
 * work records still go through their dedicated services
 * (``JobService`` for queued work; the worker-pool task tools on the
 * backend for turn/report rows).
 */
@Injectable({
  providedIn: 'root'
})
export class WorkService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/work';

  // Signals for state — matches QueueService/JobService shape so the
  // Jobs page can swap between JobService and WorkService without
  // restructuring its template or component logic.
  readonly works = signal<Work[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * GET /api/work?status=...&project_id=...&instance_id=...&kind=...&root_only=...
   *
   * Empty / undefined filter values are stripped before the request so
   * the backend only sees the params the caller actually filtered on.
   * ``root_only`` is always serialised as ``true`` or ``false`` (never
   * omitted as a bare token) so the backend ``bool`` parser never has
   * to guess.
   *
   * ERROR CONTRACT (Phase 2 — jobs-page-improvement): errors
   * PROPAGATE — this method no longer swallows failures into
   * ``of([])``. The pre-Phase-2 behavior collapsed a failed poll
   * into a healthy-looking ``[]`` emission, which the store's
   * ``.next`` callback treated as honest empty data and replaced
   * the last good payload — the exact retain-last-data violation
   * the indicator fixed for its legs and the exact lossy failure
   * mode the Phase 2 task-6 sweep was chartered to kill (the
   * snackbar-only swallow at ``jobs.component.ts:628-641``). The
   * service-level ``error`` signal still records the failure for
   * legacy readers; the fetch now also FAILS the observable so
   * callers (the ``JobsPageStore.fetchWorks`` leg) can flip
   * ``worksDegraded`` and retain the previous payload.
   *
   * ``getWork`` toggles the ``loading`` signal so direct subscribers
   * still see the spinner state.
   *
   * Args:
   *     filters: Optional filter object. All fields are optional.
   *
   * Returns:
   *     Observable<Work[]> — propagates errors. Also pushes the
   *     healthy payload into the ``works`` signal.
   */
  getWork(filters?: WorkFilters): Observable<Work[]> {
    let params = new HttpParams();
    if (filters) {
      if (filters.status) params = params.set('status', filters.status);
      if (filters.project_id) params = params.set('project_id', filters.project_id);
      if (filters.instance_id) params = params.set('instance_id', filters.instance_id);
      if (filters.kind) params = params.set('kind', filters.kind);
      if (filters.root_only !== undefined) {
        // Serialise explicitly so the query is ``root_only=true`` /
        // ``root_only=false`` — never an empty token — matching the
        // FastAPI ``bool`` coercion rules on /api/work.
        params = params.set('root_only', filters.root_only ? 'true' : 'false');
      }
    }

    // Toggle the loading signal so direct subscribers + the page
    // skeleton/spinner state still surface. ``finalize`` clears the
    // flag on BOTH the healthy and the errored path.
    this.loading.set(true);
    return this.http.get<Work[]>(this.API_BASE, { params }).pipe(
      tap((works) => this.works.set(works)),
      catchError((err) => {
        // Record the failure for legacy readers, but PROPAGATE it —
        // the store's retain-last-data discipline requires the
        // observable to fail, not emit a healthy-looking ``[]``.
        this.error.set(err?.message || 'Failed to fetch work');
        return throwError(() => err);
      }),
      finalize(() => this.loading.set(false))
    );
  }

  /**
   * Helper to refresh the works list while keeping the loading state
   * surface aligned with ``JobService.refreshJobs``.
   */
  refreshWork(filters?: WorkFilters): void {
    this.loading.set(true);
    this.getWork(filters).subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  /**
   * Helper to clear the error signal — mirrors ``QueueService.clearError``.
   */
  clearError(): void {
    this.error.set(null);
  }
}
