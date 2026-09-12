import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams, HttpResponse } from '@angular/common/http';
import { Observable, tap, catchError, of, map } from 'rxjs';
import { Job, JobCreate, JobFilters, DeadLetterItem, RetryAllResult, DLQReplayResponse, DLQListResponse } from '../models/job.model';
import { MissionListResponse } from '../models/mission.model';
import { DeferBlockedStatus } from '../models/defer-blocked.model';

interface JobListResponse {
  jobs: Job[];
  total: number;
}

/** Result of ``POST /api/jobs/defer-holders/{id}/force-complete`` (WS4). */
export interface DeferHolderForceCompleteResult {
  instance_id: string;
  terminated: boolean;
  probe_busy: boolean;
  message: string;
}

/** One per-row outcome inside ``DeferHolderResendResult``. */
export interface DeferHolderResendRow {
  cancelled_job_id: string;
  job_id?: string;
  message_id?: string;
  error?: string;
  skipped?: string;
}

/** Result of ``POST /api/jobs/defer-holders/{id}/resend-foreground`` (WS4). */
export interface DeferHolderResendResult {
  instance_id: string;
  found_defer_jobs: number;
  cancelled_defer_jobs: number;
  resend_results: DeferHolderResendRow[];
  skipped_empty_content: number;
  message: string;
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
  /**
   * Bucket 5 — number of zombie instances (non-terminal with no live
   * work) terminated by the cleanup pass. Excluded from
   * ``total_processed`` for the same reason ``orphaned_reaped`` and
   * ``reconciled_bad_state`` are — it operates on the ``instances``
   * table, not ``job_queue_items``.
   */
  terminated_instances?: number;
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
   * GET /api/jobs?status=...&source=...&agent_id=...&queue_id=...&include_deleted=...&limit=100
   *
   * P1 (jobs-page-improvement) — ALWAYS sends ``limit=100`` (the
   * backend clamps 1..100). The pre-P1 behavior sent NO limit, so the
   * backend's silent default of 50 truncated the page's window
   * invisibly (P2.1: truncated AND invisible — the worst of both
   * worlds). The full-window honesty banner lands in Phase 2.
   *
   * ERROR CONTRACT (P1): errors PROPAGATE — this method no longer
   * swallows failures into ``of([])``. The old swallow made a failed
   * fetch indistinguishable from a healthy empty poll at the call
   * site, so the store's retain-last-data contract (keep the last
   * good list on error, never flash a bare empty list) was
   * unimplementable. The service-level ``error`` signal still records
   * the failure for legacy readers; the fetch now also FAILS the
   * observable so callers decide what to retain.
   *
   * ``listRecentJobs(10)`` (indicator feed) is untouched.
   */
  listJobs(filters?: JobFilters): Observable<Job[]> {
    let params = new HttpParams()
      // P1 — explicit newest-100 window (BE clamps at 100; see
      // daemon/routers/jobs_crud.py limit parsing + constants.py).
      .set('limit', '100');
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
      tap({
        next: (jobs) => this.jobs.set(jobs),
        error: (err: unknown) => {
          const message =
            err instanceof Error ? err.message : 'Failed to fetch jobs';
          this.error.set(message);
        },
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
   * ``settled`` (M3) is the mirror-receipt terminal — included so
   * settled transport receipts surface in the Recent window next to
   * task-side ``completed`` rows.
   * Errors intentionally propagate so the caller can react via its
   * own error handler.
   */
  listRecentJobs(limit = 10): Observable<Job[]> {
    const params = new HttpParams()
      .set('status', 'completed,settled,failed,cancelled,dead_letter')
      .set('limit', limit.toString());
    return this.http.get<JobListResponse>(this.API_BASE, { params }).pipe(
      map((response) => response.jobs)
    );
  }

  /**
   * Mission-tree panel (2026-09-07, ``feature/job-queue-mission-tree``)
   * — fetch the missions projection used by both the badge's segmented
   * pill and the panel's tree.
   *
   * ``GET /api/missions`` with optional ``liveness`` filter and
   * ``limit`` (BE clamps to ``[1, MAX_PAGE_LIMIT]``, default
   * ``DEFAULT_PAGE_LIMIT`` = 10; the badge calls with limit=20 to
   * show a richer breakdown — the response's filter-aware ``total``
   * is the badge's count leg, and the segmented pill's tooltip needs
   * the per-liveness breakdown, so the full page is pulled here.
   *
   * Returns the full ``MissionSummary[]`` + ``total`` from the
   * envelope. ``null`` total ⇒ count leg degraded (NOT 0); the badge
   * falls back to ``missions.length`` defensively.
   *
   * Errors propagate so the badge's per-participant ``catchError``
   * in the forkJoin degrades to ``null`` without killing the jobs
   * intake on the same tick.
   */
  listMissions(params?: { liveness?: string; limit?: number }): Observable<MissionListResponse> {
    let httpParams = new HttpParams();
    if (params?.liveness) httpParams = httpParams.set('liveness', params.liveness);
    if (params?.limit !== undefined) httpParams = httpParams.set('limit', params.limit.toString());
    return this.http.get<MissionListResponse>('/api/missions', { params: httpParams });
  }

  /**
   * Mission-tree panel (2026-09-07, ``feature/job-queue-mission-tree``)
   * — fetch the jobs attached to a given mission.
   *
   * ``GET /api/jobs?mission_id=<id>&include_deleted=false`` (BE
   * landed this filter in 327fdc1a). Returns the raw ``Job[]`` array
   * (mapped from the response envelope so callers get rows, not the
   * wrapper). ``include_deleted=false`` keeps the panel view clean —
   * soft-deleted jobs are filtered out, matching the rest of the
   * panel's surface.
   *
   * Status: deliberately UNWIRED on the FE today (kept per the
   * leader's review decision, 2026-09-07). The panel currently
   * pulls its full mission-jobs picture from the existing
   * ``listActiveJobs`` + ``listRecentJobs`` polling — the mission
   * grouping key (coalesced ``job.mission_id ?? job.instance_id``
   * since the 2026-09-08 live-smoke fix F1; the list wire ships
   * ``mission_id: null`` for child-bound rows) is already on every
   * Job payload, so ``buildInstanceTree`` does the matching
   * client-side without a per-mission round-trip (the
   * instances-primary tree, ``feature/job-queue-instance-tree``,
   * 2026-09-08, design V1).
   *
   * Intended future use: LAZY FETCH for an expanded mission node —
   * when a user expands a node with N+ jobs, the panel can call
   * ``listJobsByMission(node.mission.mission_id)`` to stream the
   * full row list rather than paginating the global active/recent
   * windows. That work is NOT scheduled for this fix; the method is
   * here so the future consumer doesn't have to re-add it (and so
   * this comment stops people reading the unwired surface as
   * accidental dead code). Its spec stays.
   *
   * Errors propagate so a future per-mission fetch can route through
   * the same per-participant ``catchError`` isolation the badge
   * already uses.
   */
  listJobsByMission(missionId: string): Observable<Job[]> {
    const params = new HttpParams()
      .set('mission_id', missionId)
      .set('include_deleted', 'false');
    return this.http.get<JobListResponse>(this.API_BASE, { params }).pipe(
      map((response) => response.jobs),
    );
  }

  /**
   * Defer-gate block status — ``GET /api/queues/defer-blocked``.
   *
   * Consumed by the header JobQueueIndicator's warning affordance.
   * Errors (404/503 during BE rollout skew, 500, network) propagate —
   * the badge isolates this participant in its ``forkJoin`` so a
   * failure degrades the icon to hidden WITHOUT killing the jobs
   * fetch riding the same tick.
   */
  listDeferBlocked(): Observable<DeferBlockedStatus> {
    return this.http.get<DeferBlockedStatus>('/api/queues/defer-blocked');
  }

  /**
   * POST /api/jobs/defer-holders/{id}/force-complete (WS4).
   *
   * Terminate a STALLED (mirrors-only) defer-gate holder. The server
   * re-derives mirrors-only at execution time; a guard refusal comes
   * back as 200 with ``terminated=false`` — the caller inspects the
   * flag rather than the error channel.
   */
  forceCompleteDeferHolder(instanceId: string): Observable<DeferHolderForceCompleteResult> {
    return this.http
      .post<DeferHolderForceCompleteResult>(
        `${this.API_BASE}/defer-holders/${encodeURIComponent(instanceId)}/force-complete`,
        {}
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to force-complete holder');
          throw err;
        })
      );
  }

  /**
   * POST /api/jobs/defer-holders/{id}/resend-foreground (WS4).
   *
   * Cancel the holder's queued defer-lane jobs and re-send their
   * message content as NEW foreground message jobs.
   */
  resendDeferredForeground(instanceId: string): Observable<DeferHolderResendResult> {
    return this.http
      .post<DeferHolderResendResult>(
        `${this.API_BASE}/defer-holders/${encodeURIComponent(instanceId)}/resend-foreground`,
        {}
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to re-send deferred messages');
          throw err;
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
