// Jobs empty-state model — jobs-page-improvement arc, Phase 2.
//
// The "empty" branch of the page's render tree has FOUR distinct
// states that are easy to conflate:
//
// * ``loading``     — first fetch in flight, no data yet. The page
//                     renders a loading skeleton.
//
// * ``dataEmpty``   — fetched successfully, the dataset is GENUINELY
//                     empty (zero rows from the BE). Distinct copy
//                     from ``filterEmpty`` (no rows ≠ no matches).
//
// * ``filterEmpty`` — fetched successfully, the dataset has rows,
//                     but the filter projection excludes all of
//                     them. Distinct copy from ``dataEmpty``.
//                     "Adjust filters or clear them" — never "No work
//                     records yet" (which implies the project is
//                     empty, a different message).
//
// * ``errored``     — the LAST fetch failed; the page MUST render
//                     the degraded banner (the retain-last-data
//                     discipline) and the user MUST be able to retry.
//                     The data is retained underneath.
//
// The skeleton is rendered ONLY in ``loading`` — background
// refreshes during ``dataEmpty``/``filterEmpty``/``errored`` retain
// the last good data and NEVER flash a skeleton (the legacy spinner
// snackbar during background refreshes was one of the pain points
// the plan set out to kill).
//
// Pure module — no Angular imports, no side effects. Spec-able by
// direct call (see jobs-empty-state.model.spec.ts).

import type { JobsViewMode } from '../../models/jobs-filter-state.model';

/** The four empty-state branches the page can render. */
export type JobsEmptyStateKind =
  | 'loading'
  | 'dataEmpty'
  | 'filterEmpty'
  | 'errored';

/**
 * Inputs to the empty-state classifier. The classifier is a pure
 * function of these — components stay thin computeds over the
 * classifier (the "logic-mirror" convention: spec the model, not
 * the component).
 *
 * * ``loading``         — the active-view leg's loading flag.
 * * ``degraded``        — the active-view leg's degraded flag.
 * * ``hasRows``         — TRUE iff the filteredJobs computed has
 *                          at least one row. (An empty raw dataset
 *                          with zero rows after filter is also
 *                          ``false``.)
 * * ``hasActiveFilters``— TRUE iff any user-facing filter is non-
 *                          default (status multi-select, source,
 *                          agent, queue, include_deleted). Project
 *                          is a SCOPE selection, not a filter.
 * * ``viewMode``        — drives the all-work-specific copy variant.
 */
export interface JobsEmptyStateInputs {
  readonly loading: boolean;
  readonly degraded: boolean;
  readonly hasRows: boolean;
  readonly hasActiveFilters: boolean;
  readonly viewMode: JobsViewMode;
}

/**
 * Pure classifier. ``errored`` WINS over every other state — a
 * failed fetch dominates the render tree (the page MUST surface
 * the failure even if the projection happens to be empty). The
 * skeleton ``loading`` only renders when there is NO data to retain
 * (otherwise the retain-last-data discipline would be violated).
 */
export function classifyJobsEmptyState(
  inputs: JobsEmptyStateInputs,
): JobsEmptyStateKind {
  // Background refresh + last good data retained ⇒ render the list,
  // NOT a skeleton. The skeleton ONLY fires when the very first
  // fetch is in flight (no data to retain yet).
  if (inputs.degraded) {
    return 'errored';
  }
  if (inputs.hasRows) {
    // hasRows but classified as "empty" is impossible — the caller
    // branches away from the empty tree entirely. Defensive return
    // for type-completeness; not a real branch.
    return 'dataEmpty';
  }
  // No rows to show. Distinguish "first fetch in flight" from
  // "data confirmed empty" so the skeleton never flashes during a
  // background refresh.
  if (inputs.loading) {
    return 'loading';
  }
  // FilterEmpty vs dataEmpty: if ANY user-facing filter is active
  // and the projection is empty, the operator has narrowed their
  // view to zero — adjust/clear the filter to widen.
  if (inputs.hasActiveFilters) {
    return 'filterEmpty';
  }
  // Truly empty dataset.
  return 'dataEmpty';
}

/**
 * Render-ready copy for an empty-state. The component binds to
 * ``title``/``body``/``ctaLabel`` directly. Strings are pinned
 * here so the spec can grep for the EXACT copy the user sees
 * (F-5: template-source anchor lives in jobs-page.bindings.pins.spec).
 */
export interface JobsEmptyStateCopy {
  readonly title: string;
  readonly body: string;
  /** CTA label — ``null`` when no CTA (e.g. the skeleton). */
  readonly ctaLabel: string | null;
  /** Icon hint — Material ligature name. */
  readonly icon: string;
}

/**
 * Copy table. Pinned as a const so the spec can verify the EXACT
 * strings the template renders.
 *
 * Notes:
 * * ``dataEmpty`` is view-mode-aware — the all-work view says "No
 *   work records yet" (unified record set), the queues view says
 *   "No jobs found" (the legacy copy).
 * * ``loading`` has no CTA — the skeleton IS the affordance.
 * * ``filterEmpty`` ALWAYS offers "Clear filters" — the operator
 *   narrowing to zero is the only way to land here.
 */
export const JOBS_EMPTY_STATE_COPY: {
  readonly loading: JobsEmptyStateCopy;
  readonly dataEmpty: { readonly queues: JobsEmptyStateCopy; readonly allWork: JobsEmptyStateCopy };
  readonly filterEmpty: JobsEmptyStateCopy;
  readonly errored: JobsEmptyStateCopy;
} = {
  loading: {
    title: 'Loading jobs',
    body: 'Fetching the latest jobs for this project',
    ctaLabel: null,
    icon: 'hourglass_empty',
  },
  dataEmpty: {
    queues: {
      title: 'No jobs found',
      body: 'Get started by creating your first job',
      ctaLabel: 'Create Job',
      icon: 'inbox',
    },
    allWork: {
      title: 'No work records yet',
      body: 'No jobs, turns, or reports have been recorded for this project.',
      ctaLabel: null,
      icon: 'workspaces',
    },
  },
  filterEmpty: {
    title: 'No matches',
    body: 'Adjust your filters or clear them to widen the view',
    ctaLabel: 'Clear filters',
    icon: 'filter_alt_off',
  },
  errored: {
    title: 'Last refresh failed',
    body: 'Showing the previously loaded jobs — click Retry to try again',
    ctaLabel: 'Retry',
    icon: 'cloud_off',
  },
} as const;

/**
 * Resolve the copy for a given (kind, viewMode) pair. The component
 * binds the result directly to the template. Pure function — no DI,
 * no signals, no side effects.
 */
export function emptyStateCopy(
  kind: JobsEmptyStateKind,
  viewMode: JobsViewMode,
): JobsEmptyStateCopy {
  switch (kind) {
    case 'loading':
      return JOBS_EMPTY_STATE_COPY.loading;
    case 'dataEmpty':
      return viewMode === 'all-work'
        ? JOBS_EMPTY_STATE_COPY.dataEmpty.allWork
        : JOBS_EMPTY_STATE_COPY.dataEmpty.queues;
    case 'filterEmpty':
      return JOBS_EMPTY_STATE_COPY.filterEmpty;
    case 'errored':
      return JOBS_EMPTY_STATE_COPY.errored;
  }
}