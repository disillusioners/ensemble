// JobsPageStore spec — jobs-page-improvement arc, Phase 1.
//
// Drives the REAL store class (plain constructor, no Angular DI —
// fetch legs are injected). This is the ONE fetch + filter pipeline
// behind BOTH view modes; the pins here are the migrated safety net
// from jobs.component.spec.ts (root_only P-A contract, Fix C SSE
// liveness propagation, M3 terminal stamping) plus the new
// retain-last-data and cross-seam invariant pins.

import { Observable } from 'rxjs';
import { Job, JobEventPayload } from '../../models/job.model';
import { Work } from '../../models/work.model';
import { createMockJob } from '../../testing/job-test-helpers';
import { JobsPageStore, JobsPageFetchers } from './jobs-page.store';
import { JobFilters } from '../../models/job.model';
import { WorkFilters } from '../../models/work.model';
import { normalizeJobsFilterState } from '../../models/jobs-filter-state.model';

/**
 * Local Work fixture typed against the REAL model (work-test-helpers
 * re-declares a legacy Work shape that predates the P1 parity fields,
 * so it cannot carry ``started_at``/``completed_at`` in its type).
 * Defaults mirror what the ``/api/work`` wire actually ships.
 */
function makeWork(overrides: Partial<Work>): Work {
  return {
    work_id: overrides.work_id ?? 'work-1',
    kind: 'job',
    status: 'pending',
    instance_id: null,
    project_id: 'project-123',
    agent_id: 'developer',
    result_summary: null,
    error: null,
    created_at: new Date().toISOString(),
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

interface LegHarness {
  store: JobsPageStore;
  jobsNext: (jobs: Job[]) => void;
  jobsError: (err: Error) => void;
  worksNext: (works: Work[]) => void;
  worksError: (err: Error) => void;
  jobsFetcher: jest.Mock;
  worksFetcher: jest.Mock;
}

/**
 * Build a store whose fetch legs are MANUALLY-ADVANCEABLE observables
 * — tests decide when (and with what) each leg resolves, which is
 * what makes the retain-last-data pins precise.
 */
function buildStore(): LegHarness {
  let jobsEmit: ((x: Job[]) => void) | null = null;
  let jobsFail: ((e: Error) => void) | null = null;
  let worksEmit: ((x: Work[]) => void) | null = null;
  let worksFail: ((e: Error) => void) | null = null;

  const jobsFetcher = jest.fn(
    (_filters: JobFilters) =>
      new Observable<Job[]>((subscriber) => {
        jobsEmit = (jobs) => subscriber.next(jobs);
        jobsFail = (e) => subscriber.error(e);
      }),
  );
  const worksFetcher = jest.fn(
    (_filters: WorkFilters) =>
      new Observable<Work[]>((subscriber) => {
        worksEmit = (works) => subscriber.next(works);
        worksFail = (e) => subscriber.error(e);
      }),
  );

  const fetchers: JobsPageFetchers = {
    fetchJobs: jobsFetcher,
    fetchWorks: worksFetcher,
  };
  const store = new JobsPageStore(fetchers);

  return {
    store,
    jobsNext: (jobs) => jobsEmit?.(jobs),
    jobsError: (e) => jobsFail?.(e),
    worksNext: (works) => worksEmit?.(works),
    worksError: (e) => worksFail?.(e),
    jobsFetcher,
    worksFetcher,
  };
}

describe('JobsPageStore — single fetch pipeline, per-leg retain-last-data', () => {
  it('fetchJobs projects the filter state onto the wire and replaces the payload on a healthy (possibly EMPTY) 200', () => {
    const h = buildStore();
    h.store.setFilters({ status: ['failed'], project_id: 'p-1' });
    h.store.fetchJobs();

    // Wire projection: multi-status join-free array, params included.
    expect(h.jobsFetcher).toHaveBeenCalledWith({
      status: ['failed'],
      source: undefined,
      agent_id: undefined,
      project_id: 'p-1',
      queue_id: undefined,
      include_deleted: undefined,
    });

    h.jobsNext([]); // healthy empty 200 — honest data, replaces the list
    expect(h.store.jobs()).toEqual([]);
    expect(h.store.jobsDegraded()).toBe(false);
    expect(h.store.jobsError()).toBeNull();
    expect(h.store.jobsLoading()).toBe(false);
  });

  it('fetchJobs ERROR retains the last good payload (never a bare empty list)', () => {
    const h = buildStore();
    h.store.fetchJobs();
    const good = [createMockJob({ job_id: 'j-1' }), createMockJob({ job_id: 'j-2' })];
    h.jobsNext(good);
    expect(h.store.jobs()).toHaveLength(2);

    h.store.fetchJobs(); // second poll fails…
    h.jobsError(new Error('backend 503'));

    // …and the list KEEPS the last good data — a failed poll must not
    // impersonate a healthy "0 rows" result.
    expect(h.store.jobs()).toHaveLength(2);
    expect(h.store.jobsError()).toBe('backend 503');
    expect(h.store.jobsDegraded()).toBe(true);
    expect(h.store.jobsLoading()).toBe(false);
  });

  it('fetchWorks ERROR retains the last good payload, flips degraded', () => {
    const h = buildStore();
    h.store.fetchWorks();
    h.worksNext([makeWork({ work_id: 'w-1' })]);
    expect(h.store.works()).toHaveLength(1);

    h.store.fetchWorks();
    h.worksError(new Error('backend 500'));

    expect(h.store.works()).toHaveLength(1);
    expect(h.store.worksError()).toBe('backend 500');
    expect(h.store.worksDegraded()).toBe(true);
    expect(h.store.worksLoading()).toBe(false);
  });

  it('a healthy poll AFTER an error clears the degraded flag', () => {
    const h = buildStore();
    h.store.fetchJobs();
    h.jobsError(new Error('503'));
    expect(h.store.jobsDegraded()).toBe(true);

    h.store.fetchJobs();
    h.jobsNext([createMockJob({ job_id: 'fresh' })]);
    expect(h.store.jobsDegraded()).toBe(false);
    expect(h.store.jobsError()).toBeNull();
  });

  it('in-flight guard: a leg already running is never double-fired', () => {
    const h = buildStore();
    h.store.fetchJobs();
    h.store.fetchJobs(); // second call while in flight
    expect(h.jobsFetcher).toHaveBeenCalledTimes(1);

    h.jobsNext([]);
    h.store.fetchJobs(); // leg finished — fires again
    expect(h.jobsFetcher).toHaveBeenCalledTimes(2);
  });

  it('refreshActive refreshes ONLY the active view leg', () => {
    const h = buildStore();
    h.store.refreshActive();
    expect(h.jobsFetcher).toHaveBeenCalledTimes(1);
    expect(h.worksFetcher).toHaveBeenCalledTimes(0);

    h.jobsNext([]);
    h.store.setFilters({ view_mode: 'all-work' });
    h.store.refreshActive();
    expect(h.jobsFetcher).toHaveBeenCalledTimes(1);
    expect(h.worksFetcher).toHaveBeenCalledTimes(1);
  });

  it('fetchWorks receives the P-A root_only:false contract on the wire', () => {
    const h = buildStore();
    h.store.setFilters({ status: ['pending', 'processing'], project_id: 'p-9' });
    h.store.fetchWorks();
    expect(h.worksFetcher).toHaveBeenCalledWith({
      status: 'pending,processing',
      project_id: 'p-9',
      root_only: false, // the All Work view is contractually named
    });
  });
});

describe('JobsPageStore — THE pipeline projects BOTH view modes', () => {
  it('filter change re-projects BOTH datasets from the ONE pipeline (cross-seam invariant)', () => {
    const h = buildStore();
    // Seed the datasets via the store's public signals (no fetch
    // under test here — the PROJECTION is under test).
    const jobs: Job[] = [
      createMockJob({ job_id: 'j-live', status: 'processing' }),
      createMockJob({ job_id: 'j-done', status: 'completed' }),
    ];
    const works: Work[] = [
      makeWork({ work_id: 'w-live', status: 'processing' }),
      makeWork({ work_id: 'w-done', status: 'completed' }),
    ];
    h.store.jobs.set(jobs);
    h.store.works.set(works);

    // Queues view → filters over the jobs dataset.
    expect(h.store.filterState().view_mode).toBe('queues');
    h.store.setFilters({ status: ['completed'] });
    expect(h.store.filteredJobs().map(j => j.job_id)).toEqual(['j-done']);

    // SAME filter, all-work view → the SAME key now binds over the
    // work dataset through the same filteredJobs computed. This is
    // the structural kill of the pre-P1 dead-filter class: the
    // all-work view no longer bypasses filters.
    h.store.setFilters({ view_mode: 'all-work' });
    expect(h.store.filteredJobs().map(j => j.job_id)).toEqual(['w-done']);
  });

  it('all-work projection runs workToJob INSIDE the pipeline: Timeline fields survive (P1 row-parity)', () => {
    const h = buildStore();
    h.store.works.set([
      makeWork({
        work_id: 'w-1',
        started_at: '2026-09-10T01:02:03Z',
        completed_at: '2026-09-10T04:05:06Z',
        result_summary: 'did the thing',
      }),
    ]);
    h.store.setFilters({ view_mode: 'all-work' });

    const row = h.store.filteredJobs().find(j => j.job_id === 'w-1')!;
    expect(row.started_at).toBe('2026-09-10T01:02:03Z'); // NOT null — pre-P1 hard-nulled this
    expect(row.completed_at).toBe('2026-09-10T04:05:06Z');
    expect(row.result_summary).toBe('did the thing');
  });

  it('order-preserving projection: filteredJobs never re-sorts either dataset', () => {
    const h = buildStore();
    h.store.jobs.set([
      createMockJob({ job_id: 'z', status: 'pending', created_at: '2026-01-03T00:00:00Z' }),
      createMockJob({ job_id: 'a', status: 'pending', created_at: '2026-01-01T00:00:00Z' }),
    ]);
    h.store.setFilters({ status: ['pending'] });
    expect(h.store.filteredJobs().map(j => j.job_id)).toEqual(['z', 'a']);
  });

  it('clearFilters resets user filters and PRESERVES the view mode', () => {
    const h = buildStore();
    h.store.setFilters({
      view_mode: 'all-work',
      status: ['failed'],
      agent_id: 'coder',
      queue_id: 'q-1',
      include_deleted: true,
    });
    h.store.clearFilters();
    const state = h.store.filterState();
    expect(state.view_mode).toBe('all-work'); // not kicked out of the view
    expect(state.status).toEqual([]);
    expect(state.agent_id).toBeNull();
    expect(state.queue_id).toBeNull();
    expect(state.include_deleted).toBe(false);
  });
});

// ── Migrated SSE pins (Fix C §8.2 + M3) ────────────────────────────────
//
// These pins previously drove the MockJobsComponent mirror in
// jobs.component.spec.ts; the production method moved VERBATIM into
// JobsPageStore.updateJobFromSse, so they now drive the REAL class.
// Coverage preserved 1:1 + a new order-preservation pin.

describe('JobsPageStore.updateJobFromSse — mission_liveness propagation (migrated Fix C pin)', () => {
  function seedMirrorRow(h: LegHarness, liveness: Work['mission_liveness']) {
    h.store.jobs.set([
      createMockJob({
        job_id: 'mirror-1',
        status: 'completed',
        instance_id: 'leader-x',
        job_type: 'message',
        mission_liveness: liveness as Job['mission_liveness'],
      }),
    ]);
    h.store.works.set([
      makeWork({
        work_id: 'mirror-1',
        kind: 'job',
        status: 'completed',
        instance_id: 'leader-x',
        project_id: null,
        agent_id: 'developer',
        result_summary: null,
        error: null,
        job_type: 'message',
        mission_liveness: liveness,
      }),
    ]);
  }

  it('jobs[] path: terminal mission_liveness in the payload overwrites the live row (works[] path too)', () => {
    const h = buildStore();
    seedMirrorRow(h, 'processing');
    h.store.updateJobFromSse({
      job_id: 'mirror-1',
      status: 'completed',
      mission_liveness: 'completed',
    });
    expect(h.store.jobs().find(j => j.job_id === 'mirror-1')!.mission_liveness).toBe('completed');
    expect(h.store.works().find(w => w.work_id === 'mirror-1')!.mission_liveness).toBe('completed');
  });

  it('present-as-null: explicit null CLEARS, absent key KEEPS previous value', () => {
    const h = buildStore();
    seedMirrorRow(h, 'processing');

    h.store.updateJobFromSse({
      job_id: 'mirror-1',
      status: 'completed',
      mission_liveness: null,
    });
    expect(h.store.jobs().find(j => j.job_id === 'mirror-1')!.mission_liveness).toBeNull();
    expect(h.store.works().find(w => w.work_id === 'mirror-1')!.mission_liveness).toBeNull();

    seedMirrorRow(h, 'processing');
    h.store.updateJobFromSse({
      job_id: 'mirror-1',
      status: 'completed',
      // mission_liveness key ABSENT.
    });
    expect(h.store.jobs().find(j => j.job_id === 'mirror-1')!.mission_liveness).toBe('processing');
    expect(h.store.works().find(w => w.work_id === 'mirror-1')!.mission_liveness).toBe('processing');
  });
});

describe('JobsPageStore.updateJobFromSse — completed_at stamped for every wire-terminal (migrated M3 pin)', () => {
  it('stamps completed_at when status === "settled" (mirror-receipt terminal)', () => {
    const h = buildStore();
    h.store.jobs.set([createMockJob({ job_id: 'job-settled', status: 'processing', completed_at: null })]);
    h.store.updateJobFromSse({ job_id: 'job-settled', status: 'settled' });
    const stamped = h.store.jobs().find(j => j.job_id === 'job-settled')!.completed_at;
    expect(stamped).not.toBeNull();
    expect(typeof stamped).toBe('string');
    expect(Number.isFinite(new Date(stamped!).getTime())).toBe(true);
  });

  it('also stamps completed_at for the legacy terminals (completed/failed/cancelled)', () => {
    const h = buildStore();
    for (const status of ['completed', 'failed', 'cancelled'] as const) {
      const id = `job-${status}`;
      h.store.jobs.set([createMockJob({ job_id: id, status: 'processing', completed_at: null })]);
      h.store.updateJobFromSse({ job_id: id, status });
      expect(h.store.jobs().find(j => j.job_id === id)!.completed_at).not.toBeNull();
    }
  });

  it('does NOT stamp completed_at for non-terminal statuses (pending/processing/paused)', () => {
    const h = buildStore();
    for (const status of ['pending', 'processing', 'paused'] as const) {
      const id = `job-${status}`;
      h.store.jobs.set([createMockJob({ job_id: id, status: 'pending', completed_at: null })]);
      h.store.updateJobFromSse({ job_id: id, status });
      expect(h.store.jobs().find(j => j.job_id === id)!.completed_at).toBeNull();
    }
  });

  it('started_at stamps once on first processing event and stays (pre-P1 contract preserved)', () => {
    const h = buildStore();
    h.store.jobs.set([createMockJob({ job_id: 's-1', status: 'pending', started_at: null })]);
    h.store.updateJobFromSse({ job_id: 's-1', status: 'processing' });
    const first = h.store.jobs().find(j => j.job_id === 's-1')!.started_at;
    expect(first).not.toBeNull();

    h.store.updateJobFromSse({ job_id: 's-1', status: 'processing' });
    expect(h.store.jobs().find(j => j.job_id === 's-1')!.started_at).toBe(first);
  });

  it('ORDER-PRESERVING patch (merge-order rule): SSE patch keeps array order, never re-sorts', () => {
    const h = buildStore();
    h.store.jobs.set([
      createMockJob({ job_id: 'z', status: 'processing', created_at: '2026-01-03T00:00:00Z' }),
      createMockJob({ job_id: 'a', status: 'processing', created_at: '2026-01-01T00:00:00Z' }),
      createMockJob({ job_id: 'm', status: 'processing', created_at: '2026-01-02T00:00:00Z' }),
    ]);
    h.store.works.set([
      makeWork({ work_id: 'wz', status: 'processing', created_at: '2026-01-03T00:00:00Z' }),
      makeWork({ work_id: 'wa', status: 'processing', created_at: '2026-01-01T00:00:00Z' }),
    ]);

    h.store.updateJobFromSse({ job_id: 'a', status: 'completed' } as JobEventPayload);

    expect(h.store.jobs().map(j => j.job_id)).toEqual(['z', 'a', 'm']);
    expect(h.store.works().map(w => w.work_id)).toEqual(['wz', 'wa']);
    expect(h.store.jobs().find(j => j.job_id === 'a')!.status).toBe('completed');
  });
});

// ── Phase 2 banner + poll-gate derived state (BEHAVIOR pins, P3 carryover) ─
//
// P2 review Finding #2 — these computeds were source-pinned ONLY
// (the page binds them and the bindings.pins.spec pins the wiring).
// Source-pins alone pass green against a buggy computed (the same
// lesson as P2's hasRows short-circuit — a source-text pin passed
// green against a buggy ``showEmptyState``). P3 pairs source-pins
// with behavior specs: the truth tables below drive the REAL store
// and pin the view-mode-aware split. A regression that flips a leg,
// drops the degraded flag, or lies about in-flight status fails one
// of these tests.

describe('JobsPageStore — windowRowCount (Phase 2 behavior)', () => {
  it('equals filteredJobs().length (post-filter, view-mode-aware)', () => {
    const h = buildStore();
    h.store.jobs.set([
      createMockJob({ job_id: 'a', status: 'pending' }),
      createMockJob({ job_id: 'b', status: 'completed' }),
      createMockJob({ job_id: 'c', status: 'pending' }),
    ]);
    expect(h.store.windowRowCount()).toBe(3);
    h.store.setFilters({ status: ['pending'] });
    expect(h.store.windowRowCount()).toBe(2);
  });

  it('drops to 0 on an empty dataset (true zero, not a degraded impersonator)', () => {
    const h = buildStore();
    expect(h.store.windowRowCount()).toBe(0);
    h.store.jobs.set([createMockJob({ job_id: 'x' })]);
    expect(h.store.windowRowCount()).toBe(1);
    h.store.jobs.set([]); // healthy empty 200 — replaces, NOT a degraded impersonator
    expect(h.store.windowRowCount()).toBe(0);
  });

  it('reflects the all-work dataset when view_mode === "all-work"', () => {
    const h = buildStore();
    h.store.works.set([
      makeWork({ work_id: 'w-1', status: 'pending' }),
      makeWork({ work_id: 'w-2', status: 'completed' }),
    ]);
    h.store.setFilters({ view_mode: 'all-work' });
    expect(h.store.windowRowCount()).toBe(2);
  });
});

describe('JobsPageStore — windowBanner (Phase 2 behavior, banner policy truth table)', () => {
  it('hidden when filteredJobs().length < DEFAULT_WINDOW_LIMIT (99 does NOT show)', () => {
    const h = buildStore();
    const rows = Array.from({ length: 99 }, (_, i) => createMockJob({ job_id: `j-${i}` }));
    h.store.jobs.set(rows);
    expect(h.store.windowBanner()).toBe('hidden');
  });

  it('visible when filteredJobs().length === DEFAULT_WINDOW_LIMIT (BOTH-WAYS edge)', () => {
    // The exactly-at-cap edge — at 100 we cannot know whether the
    // slice is complete or truncated. The banner shows.
    const h = buildStore();
    const rows = Array.from({ length: 100 }, (_, i) => createMockJob({ job_id: `j-${i}` }));
    h.store.jobs.set(rows);
    expect(h.store.windowBanner()).toBe('visible');
  });

  it('visible when filteredJobs().length > DEFAULT_WINDOW_LIMIT', () => {
    const h = buildStore();
    const rows = Array.from({ length: 250 }, (_, i) => createMockJob({ job_id: `j-${i}` }));
    h.store.jobs.set(rows);
    expect(h.store.windowBanner()).toBe('visible');
  });
});

describe('JobsPageStore — windowDegraded (Phase 2 behavior, view-scoped)', () => {
  it('reflects jobsDegraded when view_mode === "queues"', () => {
    const h = buildStore();
    h.store.fetchJobs();
    h.jobsError(new Error('503'));
    expect(h.store.windowDegraded()).toBe(true);
    expect(h.store.jobsDegraded()).toBe(true);
    expect(h.store.worksDegraded()).toBe(false);
  });

  it('reflects worksDegraded when view_mode === "all-work"', () => {
    const h = buildStore();
    h.store.setFilters({ view_mode: 'all-work' });
    h.store.fetchWorks();
    h.worksError(new Error('500'));
    expect(h.store.windowDegraded()).toBe(true);
    expect(h.store.worksDegraded()).toBe(true);
    expect(h.store.jobsDegraded()).toBe(false);
  });

  it('a healthy poll on the ACTIVE leg clears the degraded flag (view-scoped reset)', () => {
    const h = buildStore();
    h.store.fetchJobs();
    h.jobsError(new Error('503'));
    expect(h.store.windowDegraded()).toBe(true);

    h.store.fetchJobs();
    h.jobsNext([createMockJob({ job_id: 'fresh' })]);
    expect(h.store.windowDegraded()).toBe(false);
  });

  it('the INACTIVE leg\'s degraded flag does NOT bleed into windowDegraded', () => {
    const h = buildStore();
    h.store.setFilters({ view_mode: 'queues' });
    h.store.fetchWorks();
    h.worksError(new Error('500')); // inactive leg degraded — must not surface
    expect(h.store.windowDegraded()).toBe(false);
  });
});

describe('JobsPageStore — fetchInFlight (Phase 2 behavior, view-scoped gate)', () => {
  it('true while the ACTIVE leg is loading, false when settled', () => {
    const h = buildStore();
    expect(h.store.fetchInFlight()).toBe(false);
    h.store.fetchJobs();
    expect(h.store.fetchInFlight()).toBe(true);
    h.jobsNext([createMockJob({ job_id: 'j-1' })]);
    expect(h.store.fetchInFlight()).toBe(false);
  });

  it('follows the all-work leg when view_mode === "all-work"', () => {
    const h = buildStore();
    h.store.setFilters({ view_mode: 'all-work' });
    h.store.fetchWorks();
    expect(h.store.fetchInFlight()).toBe(true);
    // The JOBS leg loading at the same time MUST NOT bleed in.
    h.store.fetchJobs();
    expect(h.store.fetchInFlight()).toBe(true);
    h.jobsNext([]);
    expect(h.store.fetchInFlight()).toBe(true); // still works leg in flight
    h.worksNext([]);
    expect(h.store.fetchInFlight()).toBe(false);
  });

  it('errors clear in-flight (the gate must NOT stay stuck shut on failure)', () => {
    const h = buildStore();
    h.store.fetchJobs();
    expect(h.store.fetchInFlight()).toBe(true);
    h.jobsError(new Error('503'));
    expect(h.store.fetchInFlight()).toBe(false); // a failed poll un-sticks the gate
  });
});

// ── Local mutation seams ───────────────────────────────────────────────

describe('JobsPageStore — local mutation seams (post-action corrections)', () => {
  it('removeJob drops exactly one row without refetch', () => {
    const h = buildStore();
    h.store.jobs.set([createMockJob({ job_id: 'a' }), createMockJob({ job_id: 'b' })]);
    h.store.removeJob('a');
    expect(h.store.jobs().map(j => j.job_id)).toEqual(['b']);
  });

  it('patchJob updates one row in place (deleted_at stamp path)', () => {
    const h = buildStore();
    h.store.jobs.set([createMockJob({ job_id: 'a' }), createMockJob({ job_id: 'b' })]);
    h.store.patchJob('b', { deleted_at: '2026-09-12T00:00:00Z' });
    expect(h.store.jobs().find(j => j.job_id === 'b')!.deleted_at).toBe('2026-09-12T00:00:00Z');
    expect(h.store.jobs().find(j => j.job_id === 'a')!.deleted_at).toBeNull();
  });

  it('findJob returns the drawer target across the jobs dataset', () => {
    const h = buildStore();
    h.store.jobs.set([createMockJob({ job_id: 'a' })]);
    expect(h.store.findJob('a')!.job_id).toBe('a');
    expect(h.store.findJob('missing')).toBeUndefined();
  });
});
