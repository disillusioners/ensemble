// jobs-empty-state.model spec — jobs-page-improvement arc, Phase 2.
//
// Pins:
// * The four-state classifier transitions: loading → dataEmpty,
//   loading → filterEmpty, any → errored (degraded WINS).
// * Filter-empty vs truly-empty distinction (the plan calls out the
//   distinction explicitly — the "No work records yet" copy implies
//   the dataset is empty, NOT that the user's filter is too narrow).
// * View-mode-aware dataEmpty copy (queues vs all-work).
// * Skeleton never flashes on background refresh (loading branch
//   only fires when there is no data to retain).

import {
  JOBS_EMPTY_STATE_COPY,
  JobsEmptyStateInputs,
  classifyJobsEmptyState,
  emptyStateCopy,
} from './jobs-empty-state.model';

function inputs(over: Partial<JobsEmptyStateInputs> = {}): JobsEmptyStateInputs {
  return {
    loading: false,
    degraded: false,
    hasRows: true,
    hasActiveFilters: false,
    viewMode: 'queues',
    ...over,
  };
}

describe('jobs-empty-state — classifyJobsEmptyState (state table)', () => {
  it('errored WINS over every other state (degraded beats loading/empty)', () => {
    expect(
      classifyJobsEmptyState(
        inputs({ degraded: true, loading: true, hasRows: false, hasActiveFilters: true }),
      ),
    ).toBe('errored');
  });

  it('loading: first fetch in flight, no data, no degradation', () => {
    expect(
      classifyJobsEmptyState(
        inputs({ loading: true, hasRows: false, hasActiveFilters: false }),
      ),
    ).toBe('loading');
  });

  it('loading with an active filter still classifies as loading (skeleton first)', () => {
    // The skeleton fires for the first fetch regardless of the
    // filter state — the user sees the loading affordance first,
    // then the post-fetch classification lands.
    expect(
      classifyJobsEmptyState(
        inputs({ loading: true, hasRows: false, hasActiveFilters: true }),
      ),
    ).toBe('loading');
  });

  it('dataEmpty: post-fetch, no rows, no filters', () => {
    expect(
      classifyJobsEmptyState(
        inputs({ loading: false, hasRows: false, hasActiveFilters: false }),
      ),
    ).toBe('dataEmpty');
  });

  it('filterEmpty: post-fetch, no rows, user has narrowed the view', () => {
    expect(
      classifyJobsEmptyState(
        inputs({ loading: false, hasRows: false, hasActiveFilters: true }),
      ),
    ).toBe('filterEmpty');
  });

  it('background refresh never flashes the skeleton (hasRows TRUE → list stays visible)', () => {
    // The COMPONENT (jobs.component.ts ``showEmptyState``) now
    // short-circuits on ``hasRows`` so the virtual list stays
    // visible. The classifier still returns ``dataEmpty`` here as a
    // defensive type-completeness arm — the production template
    // branches away via the component-level gate.
    expect(
      classifyJobsEmptyState(
        inputs({ loading: true, hasRows: true, hasActiveFilters: false }),
      ),
    ).toBe('dataEmpty');
  });

  it('hasRows + degraded: the data is retained, errored wins', () => {
    expect(
      classifyJobsEmptyState(
        inputs({ degraded: true, hasRows: true }),
      ),
    ).toBe('errored');
  });
});

describe('jobs-empty-state — copy variants (F-5 anchor)', () => {
  it('loading copy: title + body, NO CTA', () => {
    const c = JOBS_EMPTY_STATE_COPY.loading;
    expect(c.title).toBe('Loading jobs');
    expect(c.body.length).toBeGreaterThan(0);
    expect(c.ctaLabel).toBeNull();
  });

  it('dataEmpty.queues carries the legacy "No jobs found" copy', () => {
    const c = JOBS_EMPTY_STATE_COPY.dataEmpty.queues;
    expect(c.title).toBe('No jobs found');
    expect(c.ctaLabel).toBe('Create Job');
    expect(c.icon).toBe('inbox');
  });

  it('dataEmpty.allWork carries the unified "No work records yet" copy', () => {
    const c = JOBS_EMPTY_STATE_COPY.dataEmpty.allWork;
    expect(c.title).toBe('No work records yet');
    expect(c.ctaLabel).toBeNull();
    expect(c.icon).toBe('workspaces');
  });

  it('filterEmpty copy: distinguishes "no matches" from "no data"', () => {
    const c = JOBS_EMPTY_STATE_COPY.filterEmpty;
    expect(c.title).toBe('No matches');
    expect(c.ctaLabel).toBe('Clear filters');
    expect(c.icon).toBe('filter_alt_off');
    // The body must NOT claim the dataset is empty (that would be a
    // different state) — it must direct the user to widen the view.
    expect(c.body).toMatch(/filter/i);
  });

  it('errored copy: never claims previously-loaded data — the refresh may have been the FIRST fetch', () => {
    const c = JOBS_EMPTY_STATE_COPY.errored;
    expect(c.title).toBe('Last refresh failed');
    // P2 fix — the previous body claimed "Showing the previously
    // loaded jobs", but errored also fires when hasRows=false (the
    // first fetch failed). Drop the previously-loaded claim.
    expect(c.body).not.toMatch(/previously/i);
    expect(c.body).toMatch(/Retry/i);
    expect(c.ctaLabel).toBe('Retry');
    expect(c.icon).toBe('cloud_off');
  });
});

describe('jobs-empty-state — emptyStateCopy(view-mode) resolver', () => {
  it('queues + dataEmpty → queues copy', () => {
    const c = emptyStateCopy('dataEmpty', 'queues');
    expect(c.title).toBe('No jobs found');
  });

  it('all-work + dataEmpty → allWork copy', () => {
    const c = emptyStateCopy('dataEmpty', 'all-work');
    expect(c.title).toBe('No work records yet');
  });

  it('loading is view-mode-agnostic', () => {
    expect(emptyStateCopy('loading', 'queues').title).toBe('Loading jobs');
    expect(emptyStateCopy('loading', 'all-work').title).toBe('Loading jobs');
  });

  it('filterEmpty + errored are view-mode-agnostic', () => {
    expect(emptyStateCopy('filterEmpty', 'queues')).toBe(
      emptyStateCopy('filterEmpty', 'all-work'),
    );
    expect(emptyStateCopy('errored', 'queues')).toBe(
      emptyStateCopy('errored', 'all-work'),
    );
  });
});