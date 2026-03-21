import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams, HttpResponse } from '@angular/common/http';
import { Observable, tap, catchError, of, map } from 'rxjs';
import { Job, JobCreate, JobFilters } from '../models/job.model';

interface JobListResponse {
  jobs: Job[];
  total: number;
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
   * GET /api/jobs?status=...&source=...&agent_dir=...
   */
  listJobs(filters?: JobFilters): Observable<Job[]> {
    let params = new HttpParams();
    if (filters) {
      if (filters.status) params = params.set('status', filters.status);
      if (filters.source) params = params.set('source', filters.source);
      if (filters.agent_dir) params = params.set('agent_dir', filters.agent_dir);
      if (filters.project_id) params = params.set('project_id', filters.project_id);
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
