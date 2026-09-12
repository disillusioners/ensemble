// Jobs URL State codec spec — jobs-page-improvement arc, Phase 5.
//
// Drives the REAL pure functions directly (no Angular, no TestBed).
// Round-trip + hostile-input fixtures per the plan task 1 acceptance
// criteria.

import {
  JOB_QUERY_PARAM,
  JobsUrlState,
  createEmptyJobsUrlState,
  diffJobsUrlState,
  parseJobsUrlState,
  serializeJobsUrlState,
} from './jobs-url-state.model';
import {
  JobsFilterState,
  createEmptyJobsFilterState,
  normalizeJobsFilterState,
} from '../../models/jobs-filter-state.model';

describe('JobsUrlState — shape + defaults', () => {
  it('empty URL state has the empty filter + null deep-link', () => {
    const state = createEmptyJobsUrlState();
    expect(state).toEqual({
      filter: createEmptyJobsFilterState(),
      job: null,
    });
  });

  it('canonical `job` query-param key is pinned (no drift to jobId/id)', () => {
    expect(JOB_QUERY_PARAM).toBe('job');
  });
});

describe('parseJobsUrlState — round-trip', () => {
  it('empty URL ⇒ empty state (the bare-tab restore path)', () => {
    expect(parseJobsUrlState(null)).toEqual(createEmptyJobsUrlState());
    expect(parseJobsUrlState(undefined)).toEqual(createEmptyJobsUrlState());
    expect(parseJobsUrlState({})).toEqual(createEmptyJobsUrlState());
  });

  it('parse→serialize→parse round-trips a fully-populated state', () => {
    const state: JobsUrlState = {
      filter: normalizeJobsFilterState({
        status: ['completed', 'failed'],
        source: 'telegram',
        agent_id: 'coder',
        project_id: 'proj-9',
        queue_id: 'queue-3',
        include_deleted: true,
        view_mode: 'all-work',
      }),
      job: 'job-abc-123',
    };
    const round1 = parseJobsUrlState(serializeJobsUrlState(state));
    expect(round1).toEqual(state);
    expect(parseJobsUrlState(serializeJobsUrlState(round1))).toEqual(round1);
  });

  it('parse→serialize round-trips the empty state (no spurious keys)', () => {
    const parsed = parseJobsUrlState(
      serializeJobsUrlState(createEmptyJobsUrlState()),
    );
    expect(parsed).toEqual(createEmptyJobsUrlState());
  });

  it('view_mode is ALWAYS emitted so the URL is explicit about the active projection', () => {
    const serialized = serializeJobsUrlState(createEmptyJobsUrlState());
    expect(serialized['view_mode']).toBe('queues');
    // Round-trip is stable.
    const parsed = parseJobsUrlState(serialized);
    expect(parsed.filter.view_mode).toBe('queues');
  });
});

describe('parseJobsUrlState — hostile-input tolerance (P5 task 1)', () => {
  it('unknown status token is dropped; canonical ones survive', () => {
    const parsed = parseJobsUrlState({
      status: 'pending,bogus,settled',
    });
    expect(parsed.filter.status).toEqual(['pending', 'settled']);
  });

  it('unknown source/view_mode fall back to unset/default (never throw)', () => {
    const parsed = parseJobsUrlState({
      source: 'fax',
      view_mode: 'hypercube',
    });
    expect(parsed.filter.source).toBeNull();
    expect(parsed.filter.view_mode).toBe('queues');
  });

  it('empty-string `?job=` collapses to null (no deep-link)', () => {
    expect(parseJobsUrlState({ job: '' }).job).toBeNull();
    expect(parseJobsUrlState({ job: '   ' }).job).toBeNull();
  });

  it('duplicated `?job=` (string-array form) tolerates — first non-empty wins', () => {
    expect(parseJobsUrlState({ job: ['job-1', 'job-2'] }).job).toBe('job-1');
    expect(parseJobsUrlState({ job: ['', 'job-2'] }).job).toBe('job-2');
  });

  it('whitespace-padded `?job=  job-1  ` is trimmed', () => {
    expect(parseJobsUrlState({ job: '  job-1  ' }).job).toBe('job-1');
  });

  it('unknown keys are silently ignored (forward-compat)', () => {
    const parsed = parseJobsUrlState({
      status: 'pending',
      random_future_param: 'whatever',
      more_garbage: 'noise',
    });
    expect(parsed.filter.status).toEqual(['pending']);
  });

  it('GATED (GATE-COMBO-FIX) — `status=settled,failed` combo is forwarded verbatim, NOT rewritten', () => {
    // The settled-involving combo defect is BE-side arc
    // ``fix/jobs-status-combo-filter``. The codec must NOT silently
    // rewrite the combo — a user with a bookmark of
    // `?status=settled,failed` must still see the BE's (currently
    // defective) row set, NOT a silently-corrected one.
    //
    // This spec pins the current behavior: forward verbatim. When
    // the parallel arc lands and the BE row set matches the FE
    // filter, the gate is removed and the fixture flips to a
    // non-skipped behavior assertion.
    const parsed = parseJobsUrlState({ status: 'settled,failed' });
    expect(parsed.filter.status).toEqual(['settled', 'failed']);
    // Round-trip preserves the combo.
    expect(parseJobsUrlState(serializeJobsUrlState(parsed))).toEqual(parsed);
  });
});

describe('serializeJobsUrlState — minimal-emit (URL writes do not bloat)', () => {
  it('unset keys are OMITTED so the URL is stable under parse→serialize', () => {
    const out = serializeJobsUrlState({
      filter: normalizeJobsFilterState({ status: ['pending'] }),
      job: null,
    });
    expect(out).toEqual({ status: 'pending', view_mode: 'queues' });
  });

  it('a deep-link `job` is included verbatim', () => {
    const out = serializeJobsUrlState({
      filter: createEmptyJobsFilterState(),
      job: 'job-abc-123',
    });
    expect(out[JOB_QUERY_PARAM]).toBe('job-abc-123');
  });
});

describe('diffJobsUrlState — minimal URL-write patch', () => {
  it('identical states ⇒ empty diff (no URL write needed)', () => {
    const state = createEmptyJobsUrlState();
    expect(diffJobsUrlState(state, state)).toEqual({});
  });

  it('a single-key change emits ONLY the changed key', () => {
    const prev = createEmptyJobsUrlState();
    const next: JobsUrlState = {
      filter: normalizeJobsFilterState({ view_mode: 'all-work' }),
      job: null,
    };
    expect(diffJobsUrlState(prev, next)).toEqual({ view_mode: 'all-work' });
  });

  it('a removed key emits `null` so router.navigate strips it', () => {
    const prev: JobsUrlState = {
      filter: normalizeJobsFilterState({ source: 'api' }),
      job: null,
    };
    const next = createEmptyJobsUrlState();
    expect(diffJobsUrlState(prev, next)).toEqual({ source: null });
  });

  it('toggling the deep-link emits ONLY the `job` key', () => {
    const prev = createEmptyJobsUrlState();
    const next: JobsUrlState = {
      filter: createEmptyJobsFilterState(),
      job: 'job-abc-123',
    };
    expect(diffJobsUrlState(prev, next)).toEqual({ job: 'job-abc-123' });
    // And removing it.
    expect(diffJobsUrlState(next, prev)).toEqual({ job: null });
  });
});
