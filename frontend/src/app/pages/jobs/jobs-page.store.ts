// JobsPageStore — jobs-page-improvement arc, Phase 1 + Phase 2.
//
// THE single fetch + filter pipeline behind BOTH view modes of the
// Jobs page. This replaces the pre-P1 dual path (component-local
// `filteredJobs` over the jobs dataset; `worksAsJobs` BYPASSING
// filters over the work dataset) — the structural fix for the
// dead-filter class (P5.1). Every view mode is now a projection of
// `filteredJobs` over the ACTIVE dataset.
//
// The store is a plain class (no Angular DI) so plain-TS specs can
// instantiate it directly; HTTP access is injected via
// ``JobsPageFetchers`` (the page wires it to JobService/WorkService).
// Signals/computeds are created in the constructor-less field
// initializers, which is safe outside an injection context for
// ``signal``/``computed`` (no ``effect`` lives in the store — the
// page keeps service-wiring effects).
//
// Phase 2 adds banner-relevant computeds (``windowRowCount``,
// ``windowBanner``, ``degraded``, ``windowDegraded``, ``fetchInFlight``)
// so the page can drive the honesty banner + the poll gate from the
// store's source of truth WITHOUT re-deriving from ``filteredJobs``
// in the template. The store stays a thin projection owner; the
// banner POLICY lives in ``jobs-window.model.ts`` (pure).

import { computed, signal } from '@angular/core';
import { Observable } from 'rxjs';
import { Job, JobEventPayload, JobFilters, isTerminalStatus } from '../../models/job.model';
import { Work, WorkFilters, workToJob } from '../../models/work.model';
import {
  JobsFilterState,
  applyJobsFilter,
  createEmptyJobsFilterState,
  normalizeJobsFilterState,
  toJobFilters,
  toWorkFilters,
} from '../../models/jobs-filter-state.model';
import {
  DEFAULT_WINDOW_LIMIT,
  WindowBannerState,
  windowIsFull,
} from './jobs-window.model';

/**
 * The two fetch legs, injected so the store stays framework-free.
 *
 * Both legs MUST propagate errors (the page's ``JobService.listJobs``
 * was converted in P1 from its legacy swallow-to-``of([])`` contract
 * — an error that lands as a healthy-looking ``[]`` emission would
 * impersonate a successful empty poll and wipe the visible list, the
 * exact retain-last-data violation the indicator fixed for its legs).
 */
export interface JobsPageFetchers {
  fetchJobs: (filters: JobFilters) => Observable<Job[]>;
  fetchWorks: (filters: WorkFilters) => Observable<Work[]>;
}

/**
 * One store, one pipeline, both views.
 *
 * State:
 * * ``jobs`` / ``works`` — the two datasets (server-shaped rows; the
 *   Work→Job projection happens INSIDE the pipeline, never stored).
 * * ``filterState`` — the single ``JobsFilterState`` (model module).
 * * Per-leg ``*Loading`` / ``*Error`` / ``*Degraded`` flags — the
 *   indicator retain-last-data pattern: an error RETAINS the last
 *   good payload and flips ``*Degraded``; a healthy empty 200
 *   legitimately replaces the list (an empty list from a real poll
 *   is data, not degradation).
 */
export class JobsPageStore {
  private readonly fetchers: JobsPageFetchers;

  // ── Datasets ────────────────────────────────────────────────────────
  readonly jobs = signal<Job[]>([]);
  readonly works = signal<Work[]>([]);

  // ── Single filter state ─────────────────────────────────────────────
  readonly filterState = signal<JobsFilterState>(createEmptyJobsFilterState());

  // ── Per-leg fetch flags (retain-last-data discipline) ──────────────
  readonly jobsLoading = signal(false);
  readonly jobsError = signal<string | null>(null);
  /** True when the LAST jobs leg failed (payload retained, stale). */
  readonly jobsDegraded = signal(false);
  readonly worksLoading = signal(false);
  readonly worksError = signal<string | null>(null);
  /** True when the LAST works leg failed (payload retained, stale). */
  readonly worksDegraded = signal(false);

  constructor(fetchers: JobsPageFetchers) {
    this.fetchers = fetchers;
  }

  // ── THE pipeline ────────────────────────────────────────────────────

  /**
   * The unified projection: filters applied over the ACTIVE dataset
   * regardless of view mode. In all-work the ``Work`` rows are
   * projected through ``workToJob`` (row-parity mapper, carries the
   * timeline fields) INSIDE the pipeline — the mapped list is never
   * stored, so it can never drift from ``works``.
   *
   * Order-preserving by construction; NEVER re-sorted client-side
   * (the server's ``created_at DESC`` ordering is authoritative).
   */
  readonly filteredJobs = computed<Job[]>(() => {
    const state = this.filterState();
    const dataset =
      state.view_mode === 'all-work'
        ? this.works().map(workToJob)
        : this.jobs();
    return applyJobsFilter(dataset, state);
  });

  // ── Phase 2: banner + poll-gate derived state ───────────────────────
  //
  // The window POLICY lives in ``jobs-window.model.ts`` (pure); the
  // store only derives the data inputs the template + the poll-gate
  // consume. Keeping the policy outside the store is the same split
  // the deferred-block model uses — the model is spec-able in
  // isolation, the store is the data source of truth.

  /** Projected row count (post-filter). Drives the banner state. */
  readonly windowRowCount = computed<number>(() => this.filteredJobs().length);

  /**
   * Banner state — at or above ``DEFAULT_WINDOW_LIMIT`` (100) the
   * page MUST surface the honesty banner. The exactly-100 case is
   * included (BOTH-WAYS copy pinned in the window model).
   */
  readonly windowBanner = computed<WindowBannerState>(() =>
    windowIsFull(this.windowRowCount(), DEFAULT_WINDOW_LIMIT),
  );

  /**
   * ``true`` iff EITHER leg's last fetch failed and the payload is
   * retained but stale. Drives the degraded banner + the errored
   * empty-state branch.
   */
  readonly degraded = computed<boolean>(
    () => this.jobsDegraded() || this.worksDegraded(),
  );

  /**
   * View-mode-aware degraded flag — the ACTIVE leg's degraded bit.
   * The page uses this to gate the banner copy ("last refresh
   * failed" wording), which is view-scoped (jobs leg vs work leg).
   */
  readonly windowDegraded = computed<boolean>(() => {
    return this.filterState().view_mode === 'all-work'
      ? this.worksDegraded()
      : this.jobsDegraded();
  });

  /**
   * ``true`` while the ACTIVE leg's fetch is in flight. Drives the
   * poll gate (the ``fetchInFlight`` pause branch) so the spec can
   * pin the gate WITHOUT a separate "is the store loading?"
   * computation.
   */
  readonly fetchInFlight = computed<boolean>(() => {
    return this.filterState().view_mode === 'all-work'
      ? this.worksLoading()
      : this.jobsLoading();
  });

  /**
   * First non-null error across both legs — the legacy store
   * exposes ``jobsError`` / ``worksError`` directly; this surfaces
   * the unified error string the degraded banner consumes.
   */
  readonly error = computed<string | null>(() => {
    return this.jobsError() ?? this.worksError() ?? null;
  });

  // ── Filter mutation ─────────────────────────────────────────────────

  /**
   * Merge a partial patch into the filter state (re-normalized —
   * invalid values cannot enter the state). Does NOT fetch; callers
   * trigger the legs their key affects (see the page handlers).
   */
  setFilters(patch: Partial<JobsFilterState>): void {
    this.filterState.update((state) =>
      normalizeJobsFilterState({ ...state, ...patch }),
    );
  }

  /**
   * Reset every USER-FACING filter while PRESERVING the view mode —
   * "clear all filters" must not kick the operator out of the view
   * they are in.
   */
  clearFilters(): void {
    this.filterState.update((state) =>
      normalizeJobsFilterState({
        ...createEmptyJobsFilterState(),
        view_mode: state.view_mode,
      }),
    );
  }

  // ── Fetch legs (per-leg catchError → retain last data) ─────────────

  /**
   * Jobs leg (``GET /api/jobs`` via the injected fetcher).
   *
   * Retain-last-data: on error the payload is NOT touched — a failed
   * poll must never impersonate a healthy empty list. A healthy
   * (possibly empty) 200 REPLACES the payload — an empty list from a
   * real poll is honest data.
   *
   * In-flight guard: a leg already running is never double-fired
   * (matches the pre-P1 poll guard; the manual refresh button now
   * shares it, which also kills the overlapping-fetch race).
   */
  fetchJobs(): void {
    if (this.jobsLoading()) {
      return;
    }
    this.jobsLoading.set(true);
    this.jobsError.set(null);
    this.fetchers
      .fetchJobs(toJobFilters(this.filterState()))
      .subscribe({
        next: (jobs) => {
          this.jobs.set(jobs);
          this.jobsDegraded.set(false);
          this.jobsLoading.set(false);
        },
        error: (err) => {
          // Retain-last-data — do NOT touch ``this.jobs`` here.
          this.jobsError.set(err?.message || 'Failed to load jobs');
          this.jobsDegraded.set(true);
          this.jobsLoading.set(false);
        },
      });
  }

  /**
   * Work leg (``GET /api/work`` via the injected fetcher).
   *
   * The fetcher receives ``root_only: false`` (via ``toWorkFilters``)
   * — the All Work view is contractually named: child-instance rows
   * stay visible (P-A). Same retain-last-data contract as the jobs
   * leg.
   */
  fetchWorks(): void {
    if (this.worksLoading()) {
      return;
    }
    this.worksLoading.set(true);
    this.worksError.set(null);
    this.fetchers.fetchWorks(toWorkFilters(this.filterState())).subscribe({
      next: (works) => {
        this.works.set(works);
        this.worksDegraded.set(false);
        this.worksLoading.set(false);
      },
      error: (err) => {
        // Retain-last-data — do NOT touch ``this.works`` here.
        this.worksError.set(err?.message || 'Failed to load unified work list');
        this.worksDegraded.set(true);
        this.worksLoading.set(false);
      },
    });
  }

  /**
   * Refresh the ACTIVE view's leg (the 30s poll and the Refresh
   * button both land here; the other leg keeps its last payload).
   */
  refreshActive(): void {
    if (this.filterState().view_mode === 'all-work') {
      this.fetchWorks();
    } else {
      this.fetchJobs();
    }
  }

  // ── SSE patching (moved verbatim from the pre-P1 component) ────────

  /**
   * Patch BOTH datasets in place for one SSE status event.
   *
   * ORDER-PRESERVING: ``signal.update(list => list.map(...))`` keeps
   * array order — the server's ordering stays authoritative; the
   * store never re-sorts (merge-order rule).
   *
   * Present-as-null semantics for every Fix C split field (identical
   * on both paths):
   * * key absent on the payload  → keep previous value (stale-tolerant)
   * * key present + value ``null`` → degraded-lookup, clear the field
   * * key present + non-null      → overwrite
   *
   * The ``in`` check is the only way to distinguish "wire didn't
   * carry the field" from "wire explicitly said null"; ``??`` would
   * collapse the two and pin stale liveness through degraded windows.
   *
   * M3 (mission-class, 2026-09-03) — ``completed_at`` is stamped for
   * any wire-terminal status, including the mirror-receipt terminal
   * ``settled`` (the pre-M3 subset missed settled terminals).
   */
  updateJobFromSse(status: JobEventPayload): void {
    const nextJobType: Job['job_type'] = 'job_type' in status
      ? (status.job_type ?? null) as Job['job_type']
      : undefined; // undefined → keep via spread
    const nextMissionLiveness: Job['mission_liveness'] = 'mission_liveness' in status
      ? (status.mission_liveness ?? null)
      : undefined; // undefined → keep via spread

    this.jobs.update(jobs =>
      jobs.map(job =>
        job.job_id === status.job_id
          ? {
              ...job,
              status: status.status || job.status,
              queue_id: status.queue_id ?? job.queue_id,
              instance_id: status.instance_id || job.instance_id,
              result_summary: status.result_summary || job.result_summary,
              error_message: status.error_message || job.error_message,
              completed_at: status.status && isTerminalStatus(status.status)
                ? new Date().toISOString()
                : job.completed_at,
              started_at: status.status === 'processing' && !job.started_at
                ? new Date().toISOString()
                : job.started_at,
              ...(nextJobType !== undefined ? { job_type: nextJobType } : {}),
              ...(nextMissionLiveness !== undefined ? { mission_liveness: nextMissionLiveness } : {}),
            }
          : job
      )
    );

    // The unified Work list patches on the SAME event — the SSE
    // payload uses ``job_id`` as the work_id key (the backend SSE
    // endpoint resolves work_id through WorkResolverService). Same
    // present-as-null contract as the jobs path.
    this.works.update(works =>
      works.map(work =>
        work.work_id === status.job_id
          ? {
              ...work,
              status: status.status || work.status,
              instance_id: status.instance_id ?? work.instance_id,
              result_summary: status.result_summary ?? work.result_summary,
              error: status.error_message ?? work.error,
              ...(nextJobType !== undefined ? { job_type: nextJobType } : {}),
              ...(nextMissionLiveness !== undefined ? { mission_liveness: nextMissionLiveness } : {}),
            }
          : work
      )
    );
  }

  // ── Local-mutation seams (post-action list corrections) ────────────

  /**
   * Remove a job row locally (soft-delete with "Show Deleted" off —
   * the row must leave the list without a refetch round-trip).
   */
  removeJob(jobId: string): void {
    this.jobs.update(jobs => jobs.filter(j => j.job_id !== jobId));
  }

  /**
   * Patch a single job row locally (soft-delete with "Show Deleted"
   * on — the row stays, stamped ``deleted_at`` so the card renders
   * its deleted state).
   */
  patchJob(jobId: string, patch: Partial<Job>): void {
    this.jobs.update(jobs =>
      jobs.map(j => (j.job_id === jobId ? { ...j, ...patch } : j)),
    );
  }

  /** Find a job row across the jobs dataset (drawer action seam). */
  findJob(jobId: string): Job | undefined {
    return this.jobs().find(j => j.job_id === jobId);
  }
}
