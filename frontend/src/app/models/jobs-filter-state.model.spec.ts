// JobsFilterState model spec — jobs-page-improvement arc, Phase 1.
//
// Drives the REAL pure model functions directly (no Angular, no
// TestBed, no mirrors): this is the ONE filter state behind BOTH view
// modes, so its contract is pinned here before the store and template
// pins build on it.

import {
  JOB_SOURCE_VALUES,
  JOB_STATUS_VALUES,
  JobsFilterState,
  applyJobsFilter,
  asJobSource,
  asJobStatus,
  asJobsViewMode,
  createEmptyJobsFilterState,
  hasActiveJobsFilter,
  normalizeJobsFilterState,
  parseJobsFilterState,
  serializeJobsFilterState,
  toJobFilters,
  toWorkFilters,
} from './jobs-filter-state.model';
import { Job, JobStatus } from './job.model';
import { createMockJob } from '../testing/job-test-helpers';

function makeJob(overrides: Partial<Job>): Job {
  return createMockJob(overrides);
}

describe('JobsFilterState — shape + defaults', () => {
  it('empty state has every key unset and defaults to the queues view', () => {
    const state = createEmptyJobsFilterState();
    expect(state).toEqual({
      status: [],
      source: null,
      agent_id: null,
      project_id: null,
      queue_id: null,
      include_deleted: false,
      view_mode: 'queues',
    });
  });

  it('canonical status/source tables cover the model unions (exhaustiveness guard compiles)', () => {
    // The model file carries compile-time exhaustiveness assignments;
    // this runtime sanity pin keeps the lists honest if someone
    // edits them by hand.
    expect([...JOB_STATUS_VALUES].sort()).toEqual(
      ['cancelled', 'completed', 'dead_letter', 'failed', 'paused', 'pending', 'processing', 'settled'].sort(),
    );
    expect([...JOB_SOURCE_VALUES].sort()).toEqual(['api', 'scheduler', 'telegram', 'webhook'].sort());
  });

  it('narrowing helpers accept canonical values and reject unknowns', () => {
    expect(asJobStatus('settled')).toBe('settled');
    expect(asJobStatus('legacy_active')).toBeNull();
    expect(asJobSource('telegram')).toBe('telegram');
    expect(asJobSource('carrier-pigeon')).toBeNull();
    expect(asJobsViewMode('all-work')).toBe('all-work');
    expect(asJobsViewMode('tree')).toBeNull();
  });
});

describe('JobsFilterState — normalizeJobsFilterState (unknown-value tolerance)', () => {
  it('returns the empty state for null/undefined input', () => {
    expect(normalizeJobsFilterState(null)).toEqual(createEmptyJobsFilterState());
    expect(normalizeJobsFilterState(undefined)).toEqual(createEmptyJobsFilterState());
  });

  it('drops unknown statuses, keeps canonical ones, de-duplicates', () => {
    const state = normalizeJobsFilterState({
      status: ['pending', 'bogus_status', 'settled', 'pending'] as JobStatus[],
    });
    expect(state.status).toEqual(['pending', 'settled']);
  });

  it('collapses empty strings and whitespace-only ids to "no filter"', () => {
    const state = normalizeJobsFilterState({
      agent_id: '   ',
      project_id: '',
      queue_id: ' queue-1 ',
    });
    expect(state.agent_id).toBeNull();
    expect(state.project_id).toBeNull();
    expect(state.queue_id).toBe('queue-1');
  });

  it('unknown source and view_mode fall back to unset/default (never throw)', () => {
    const state = normalizeJobsFilterState({
      source: 'fax' as never,
      view_mode: 'hypercube' as never,
    });
    expect(state.source).toBeNull();
    expect(state.view_mode).toBe('queues');
  });

  it('include_deleted only accepts strict true', () => {
    expect(normalizeJobsFilterState({ include_deleted: true }).include_deleted).toBe(true);
    expect(normalizeJobsFilterState({ include_deleted: 'true' as never }).include_deleted).toBe(false);
  });
});

describe('JobsFilterState — wire projections', () => {
  it('toJobFilters omits unset values (never empty tokens on the wire)', () => {
    const filters = toJobFilters(createEmptyJobsFilterState());
    expect(filters).toEqual({
      status: undefined,
      source: undefined,
      agent_id: undefined,
      project_id: undefined,
      queue_id: undefined,
      include_deleted: undefined,
    });
  });

  it('toJobFilters forwards set values verbatim (combos forwarded, not compensated)', () => {
    const state = normalizeJobsFilterState({
      status: ['settled', 'failed'],
      source: 'api',
      agent_id: 'leader',
      project_id: 'p-1',
      queue_id: 'q-1',
      include_deleted: true,
    });
    expect(toJobFilters(state)).toEqual({
      status: ['settled', 'failed'],
      source: 'api',
      agent_id: 'leader',
      project_id: 'p-1',
      queue_id: 'q-1',
      include_deleted: true,
    });
  });

  it('toWorkFilters sends only status+project_id and pins root_only:false (P-A contract)', () => {
    const state = normalizeJobsFilterState({
      status: ['pending', 'processing'],
      project_id: 'p-1',
      queue_id: 'q-1',
      source: 'api',
      agent_id: 'leader',
      include_deleted: true,
    });
    expect(toWorkFilters(state)).toEqual({
      status: 'pending,processing',
      project_id: 'p-1',
      root_only: false,
    });
  });

  it('toWorkFilters omits status when the list is empty', () => {
    expect(toWorkFilters(createEmptyJobsFilterState())).toEqual({
      status: undefined,
      project_id: undefined,
      root_only: false,
    });
  });
});

describe('JobsFilterState — URL round-trip (Phase 5 readiness)', () => {
  it('parse→serialize→parse round-trips a fully-populated state', () => {
    const state = normalizeJobsFilterState({
      status: ['completed', 'failed'],
      source: 'telegram',
      agent_id: 'coder',
      project_id: 'proj-9',
      queue_id: 'queue-3',
      include_deleted: true,
      view_mode: 'all-work',
    });
    const parsed = parseJobsFilterState(serializeJobsFilterState(state));
    expect(parsed).toEqual(state);
    expect(parseJobsFilterState(serializeJobsFilterState(parsed))).toEqual(parsed);
  });

  it('parse→serialize round-trips the empty state', () => {
    const parsed = parseJobsFilterState(serializeJobsFilterState(createEmptyJobsFilterState()));
    expect(parsed).toEqual(createEmptyJobsFilterState());
  });

  it('parse tolerates unknown values without throwing (hand-edited URLs)', () => {
    const parsed = parseJobsFilterState({
      status: 'pending,bogus',
      source: 'smoke-signal',
      view_mode: 'nope',
      agent_id: 'leader',
    });
    expect(parsed.status).toEqual(['pending']);
    expect(parsed.source).toBeNull();
    expect(parsed.view_mode).toBe('queues');
    expect(parsed.agent_id).toBe('leader');
  });

  it('parse of null/undefined yields the empty state', () => {
    expect(parseJobsFilterState(null)).toEqual(createEmptyJobsFilterState());
    expect(parseJobsFilterState(undefined)).toEqual(createEmptyJobsFilterState());
  });
});

describe('applyJobsFilter — THE one pipeline (both view modes)', () => {
  const rows: Job[] = [
    makeJob({ job_id: '1', status: 'pending', source: 'api', agent_id: 'coder', queue_id: 'q-1' }),
    makeJob({ job_id: '2', status: 'completed', source: 'telegram', agent_id: 'leader', queue_id: 'q-2' }),
    makeJob({ job_id: '3', status: 'settled', source: 'api', agent_id: 'coder', queue_id: null }),
  ];

  it('queues view: status + source + agent + queue all filter', () => {
    const queuesState = normalizeJobsFilterState({
      status: ['pending', 'settled'],
      source: 'api',
      agent_id: 'coder',
      queue_id: 'q-1',
      view_mode: 'queues',
    });
    expect(applyJobsFilter(rows, queuesState).map(j => j.job_id)).toEqual(['1']);
  });

  it('all-work view: status + agent filter the SAME rows (cross-seam keys)', () => {
    const allWorkState = normalizeJobsFilterState({
      status: ['pending', 'settled'],
      agent_id: 'coder',
      view_mode: 'all-work',
    });
    expect(applyJobsFilter(rows, allWorkState).map(j => j.job_id)).toEqual(['1', '3']);
  });

  it('cross-seam invariant: same state (no view-scoped keys) ⇒ same row set in both modes', () => {
    const shared = { status: ['settled'] as JobStatus[], agent_id: 'coder' };
    const queues = applyJobsFilter(rows, normalizeJobsFilterState({ ...shared, view_mode: 'queues' }));
    const allWork = applyJobsFilter(rows, normalizeJobsFilterState({ ...shared, view_mode: 'all-work' }));
    expect(queues.map(j => j.job_id)).toEqual(allWork.map(j => j.job_id));
  });

  it('all-work view: queue/source/include_deleted are view-scoped and NEVER hide rows', () => {
    const state = normalizeJobsFilterState({
      queue_id: 'q-1',
      source: 'api',
      include_deleted: true,
      view_mode: 'all-work',
    });
    // Work rows carry no queue_id/source at all — applying those keys
    // would silently empty the list behind hidden controls.
    expect(applyJobsFilter(rows, state).map(j => j.job_id)).toEqual(['1', '2', '3']);
  });

  it('ORDER-PRESERVING (merge-order rule): filtering never re-sorts', () => {
    const unordered: Job[] = [
      makeJob({ job_id: 'z', status: 'pending', created_at: '2026-01-03T00:00:00Z' }),
      makeJob({ job_id: 'a', status: 'pending', created_at: '2026-01-01T00:00:00Z' }),
      makeJob({ job_id: 'm', status: 'pending', created_at: '2026-01-02T00:00:00Z' }),
    ];
    const state = normalizeJobsFilterState({ status: ['pending'] });
    // Server order is authority — even when it contradicts created_at.
    expect(applyJobsFilter(unordered, state).map(j => j.job_id)).toEqual(['z', 'a', 'm']);
  });

  it('no filters ⇒ identity (same array contents, same order)', () => {
    expect(applyJobsFilter(rows, createEmptyJobsFilterState())).toEqual(rows);
  });
});

describe('hasActiveJobsFilter', () => {
  it('false for the empty state (project_id alone is a scope, not a filter)', () => {
    expect(hasActiveJobsFilter(normalizeJobsFilterState({ project_id: 'p-1' }))).toBe(false);
  });

  it('true for any user-facing filter key', () => {
    expect(hasActiveJobsFilter(normalizeJobsFilterState({ status: ['failed'] }))).toBe(true);
    expect(hasActiveJobsFilter(normalizeJobsFilterState({ source: 'api' }))).toBe(true);
    expect(hasActiveJobsFilter(normalizeJobsFilterState({ agent_id: 'x' }))).toBe(true);
    expect(hasActiveJobsFilter(normalizeJobsFilterState({ queue_id: 'q' }))).toBe(true);
    // include_deleted is a visibility toggle, not a narrowing filter —
    // it does not count (matches pre-P1 hasActiveFilters semantics).
    expect(hasActiveJobsFilter(normalizeJobsFilterState({ include_deleted: true }))).toBe(false);
  });
});
