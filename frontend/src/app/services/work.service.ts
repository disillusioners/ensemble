import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of, finalize } from 'rxjs';
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
   * to guess. On error the works signal is left untouched and
   * ``error`` is set — callers can opt to read the latest error via
   * the ``error()`` signal or display a toast.
   *
   * Args:
   *     filters: Optional filter object. All fields are optional.
   *
   * Returns:
   *     Observable<Work[]> — also pushed into the ``works`` signal.
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

    // Mirror ``refreshWork`` — toggle the loading signal so the
    // Jobs page's skeleton/spinner state also surfaces for callers
    // that subscribe to ``getWork`` directly (e.g. ``loadWorks``).
    this.loading.set(true);
    return this.http.get<Work[]>(this.API_BASE, { params }).pipe(
      tap((works) => this.works.set(works)),
      catchError((err) => {
        this.error.set(err?.message || 'Failed to fetch work');
        return of([] as Work[]);
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
