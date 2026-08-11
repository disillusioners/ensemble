import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams, HttpResponse } from '@angular/common/http';
import { Observable, tap, catchError, of, map } from 'rxjs';
import { Job, JobCreate, JobFilters, DeadLetterItem, RetryAllResult, DLQReplayResponse, DLQListResponse } from '../models/job.model';

interface JobListResponse {
  jobs: Job[];
  total: number;
}

/**
 * Result payload returned by ``POST /api/jobs/cleanup``.
 *
 * Counters come straight from the backend so the UI can show
 * "Cancelled N queued, M active jobs" without a second round-trip.
 */
export interface JobCleanupResult {
  cancelled_queued: number;
  cancelled_active: number;
  orphaned_reaped?: number;
  /**
   * Phase 4 — number of bad-state tasks (paused/pending whose
   * linked JobItem is already terminal) reconciled to CANCELLED by
   * the cleanup pass. Excluded from ``total_processed`` for the
   * same reason ``orphaned_reaped`` is — see backend
   * ``JobQueueService.cleanup_non_terminal_jobs`` for the
   * ``validate_total_processed`` invariant.
   */
  reconciled_bad_state?: number;
  total_processed: number;
}

@Injectable({
  providedIn: 'root'
})
export class JobService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/jobs';

  // Signals for state
  readonly jobs = signal<Job[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * GET /api/jobs?status=...&source=...&agent_id=...&queue_id=...&include_deleted=...
   */
  listJobs(filters?: JobFilters): Observable<Job[]> {
    let params = new HttpParams();
    if (filters) {
      if (filters.status && filters.status.length > 0) {
        params = params.set('status', filters.status.join(','));
      }
      if (filters.source) params = params.set('source', filters.source);
      if (filters.agent_id) params = params.set('agent_id', filters.agent_id);
      if (filters.project_id) params = params.set('project_id', filters.project_id);
      if (filters.queue_id) params = params.set('queue_id', filters.queue_id);
      if (filters.include_deleted) params = params.set('include_deleted', 'true');
    }

    return this.http.get<JobListResponse>(this.API_BASE, { params }).pipe(
      map((response) => response.jobs),
      tap((jobs) => this.jobs.set(jobs)),
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch jobs');
        return of([]);
      })
    );
  }

  /**
   * GET /api/jobs?status=queued,active
   *
   * Fetches pending (queued) and active (running) jobs only —
   * used by the header JobQueueIndicator to render a live count
   * and per-project breakdown tooltip. The backend treats
   * ``queued`` and ``active`` as its internal lifecycle names;
   * we map from the public ``pending`` / ``processing`` enum
   * here so the rest of the UI keeps using the
   * ``JobStatus`` model.
   *
   * Note: errors intentionally propagate so the caller (the
   * JobQueueIndicator's ``forkJoin``) can react uniformly via
   * its single error handler.
   */
  listActiveJobs(): Observable<Job[]> {
    const params = new HttpParams().set('status', 'queued,active');
    return this.http.get<JobListResponse>(this.API_BASE, { params }).pipe(
      map((response) => response.jobs)
    );
  }

  /**
   * Fetch recently completed/failed/cancelled jobs (terminal states).
   * Errors intentionally propagate so the caller can react via its
   * own error handler.
   */
  listRecentJobs(limit = 10): Observable<Job[]> {
    const params = new HttpParams()
      .set('status', 'completed,failed,cancelled,dead_letter')
      .set('limit', limit.toString());
    return this.http.get<JobListResponse>(this.API_BASE, { params }).pipe(
      map((response) => response.jobs)
    );
  }

  /**
   * GET /api/jobs/{id}
   */
  getJob(jobId: string): Observable<Job> {
    return this.http.get<Job>(`${this.API_BASE}/${encodeURIComponent(jobId)}`).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch job');
        throw err;
      })
    );
  }

  /**
   * POST /api/jobs
   */
  createJob(job: JobCreate): Observable<Job> {
    return this.http.post<Job>(this.API_BASE, job).pipe(
      tap((createdJob) => {
        this.jobs.update((jobs) => [createdJob, ...jobs]);
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to create job');
        throw err;
      })
    );
  }

  /**
   * DELETE /api/jobs/{id}
   */
  cancelJob(jobId: string): Observable<void> {
    return this.http.delete<void>(`${this.API_BASE}/${encodeURIComponent(jobId)}`).pipe(
      tap(() => {
        this.jobs.update((jobs) =>
          jobs.map((job) =>
            job.job_id === jobId
              ? { ...job, status: 'cancelled' as const, cancelled_at: new Date().toISOString() }
              : job
          )
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to cancel job');
        throw err;
      })
    );
  }

  /**
   * POST /api/jobs/{id}/retry
   */
  retryJob(jobId: string): Observable<Job> {
    return this.http.post<Job>(`${this.API_BASE}/${encodeURIComponent(jobId)}/retry`, {}).pipe(
      tap((retriedJob) => {
        this.jobs.update((jobs) =>
          jobs.map((job) => (job.job_id === jobId ? retriedJob : job))
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to retry job');
        throw err;
      })
    );
  }

  /**
   * DELETE /api/jobs/{id} - Soft delete a job
   */
  softDeleteJob(jobId: string): Observable<Job> {
    return this.http.delete<Job>(`${this.API_BASE}/${encodeURIComponent(jobId)}`).pipe(
      tap((deletedJob) => {
        this.jobs.update((jobs) =>
          jobs.map((job) => (job.job_id === jobId ? deletedJob : job))
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to delete job');
        throw err;
      })
    );
  }

  /**
   * POST /api/jobs/{id}/restore - Restore a soft-deleted job
   */
  restoreJob(jobId: string): Observable<Job> {
    return this.http.post<Job>(`${this.API_BASE}/${encodeURIComponent(jobId)}/restore`, {}).pipe(
      tap((restoredJob) => {
        this.jobs.update((jobs) =>
          jobs.map((job) => (job.job_id === jobId ? restoredJob : job))
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to restore job');
        throw err;
      })
    );
  }

  // Dead Letter Queue Methods

  /**
   * GET /api/projects/{projectId}/dlq
   */
  listDeadLetterItems(projectId: string): Observable<DeadLetterItem[]> {
    return this.http.get<DLQListResponse>(`/api/projects/${encodeURIComponent(projectId)}/dlq`).pipe(
      map((response) => response.items),
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch dead letter items');
        throw err;
      })
    );
  }

  /**
   * POST /api/projects/{projectId}/dlq/{dlqId}/replay
   */
  retryDeadLetterJob(projectId: string, dlqId: string): Observable<DLQReplayResponse> {
    return this.http.post<DLQReplayResponse>(`/api/projects/${encodeURIComponent(projectId)}/dlq/${encodeURIComponent(dlqId)}/replay`, {}).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to replay dead letter job');
        throw err;
      })
    );
  }

  /**
   * POST /api/projects/{projectId}/dlq/replay-all
   */
  retryAllDeadLetterJobs(projectId: string): Observable<RetryAllResult> {
    return this.http.post<RetryAllResult>(`/api/projects/${encodeURIComponent(projectId)}/dlq/replay-all`, {}).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to replay all dead letter jobs');
        throw err;
      })
    );
  }

  /**
   * POST /api/jobs/cleanup
   *
   * Asks the backend to cancel every queued and active job across
   * all projects. Used by the "System Cleanup" action on the Jobs
   * page. Failures surface through the shared ``error`` signal and
   * are re-thrown so the caller can render a snackbar.
   */
  cleanupAllJobs(): Observable<JobCleanupResult> {
    return this.http.post<JobCleanupResult>(`${this.API_BASE}/cleanup`, {}).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to cleanup jobs');
        throw err;
      })
    );
  }

  /**
   * Helper to refresh jobs list
   */
  refreshJobs(filters?: JobFilters): void {
    this.loading.set(true);
    this.listJobs(filters).subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  /**
   * Helper to clear error
   */
  clearError(): void {
    this.error.set(null);
  }
}
