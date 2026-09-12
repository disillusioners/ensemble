// Jobs Filter State — jobs-page-improvement arc, Phase 1.
//
// The ONE filter-state shape behind BOTH view modes. Before P1 the
// page kept filter state in the component (`filters` signal) and ran
// TWO pipelines: `filteredJobs` over the jobs dataset (queues view)
// and `worksAsJobs` — which BYPASSED filters entirely — over the work
// dataset (all-work view). That dual path is the dead-filter bug
// class (P5.1: source/agent/show-deleted no-ops in all-work). This
// model + `JobsPageStore` replace it: one state, one pipeline, every
// view mode a projection.
//
// Pure module — no Angular imports, no side effects. Everything here
// is spec-able by direct call (see jobs-filter-state.model.spec.ts).

import { Job, JobFilters, JobSource, JobStatus } from './job.model';
import { WorkFilters } from './work.model';

/**
 * Top-level view mode for the Jobs page.
 *
 * * ``'queues'``   — the legacy "Queues" view: queue sidebar on the
 *   left, jobs fetched via ``JobService``.
 * * ``'all-work'`` — the unified work list backed by ``WorkService``
 *   (``GET /api/work``), projected onto the same ``Job`` card shape.
 *
 * Lives here (not on the page component) because it is part of the
 * serializable filter state (Phase 5 puts it in the URL). The page
 * component re-exports it for backward compatibility.
 */
export type JobsViewMode = 'queues' | 'all-work';

/** Canonical ``JobStatus`` members — single source for validation. */
export const JOB_STATUS_VALUES = [
  'pending',
  'processing',
  'paused',
  'completed',
  'settled',
  'failed',
  'cancelled',
  'dead_letter',
] as const;

// Compile-time exhaustiveness guard: if a ``JobStatus`` member is
// added to job.model.ts without updating JOB_STATUS_VALUES (or vice
// versa) this line stops compiling.
const _statusValuesCoverJobStatusUnion: readonly JobStatus[] = JOB_STATUS_VALUES;
void _statusValuesCoverJobStatusUnion;

/** Canonical ``JobSource`` members — single source for validation. */
export const JOB_SOURCE_VALUES = ['api', 'telegram', 'scheduler', 'webhook'] as const;

// Same exhaustiveness guard for sources.
const _sourceValuesCoverJobSourceUnion: readonly JobSource[] = JOB_SOURCE_VALUES;
void _sourceValuesCoverJobSourceUnion;

/**
 * The unified filter state for the Jobs page.
 *
 * ``null`` / empty-array mean "no filter" (NOT "match nothing");
 * every helper below normalizes toward that reading. This shape is
 * URL-ready (Phase 5 wires serialize/parse into the router).
 */
export interface JobsFilterState {
  /** Multi-select status filter — empty array = no filter. */
  status: JobStatus[];
  /** Client-side source filter (window-scoped; queues view only). */
  source: JobSource | null;
  /** Client-side agent filter (window-scoped; BOTH views). */
  agent_id: string | null;
  /** Server-side project scope (sent on both wire fetches). */
  project_id: string | null;
  /** Server-side queue scope (queues view only — the queue sidebar is
   * absent in all-work, and work rows carry no queue_id). */
  queue_id: string | null;
  /** Server-side soft-deleted toggle (queues view only — ``/api/work``
   * has no deleted concept, ``_query_jobs`` hides soft-deleted). */
  include_deleted: boolean;
  /** Which dataset is active — a projection selector, not a pipeline. */
  view_mode: JobsViewMode;
}

/** The "no filters, default view" state. */
export function createEmptyJobsFilterState(): JobsFilterState {
  return {
    status: [],
    source: null,
    agent_id: null,
    project_id: null,
    queue_id: null,
    include_deleted: false,
    view_mode: 'queues',
  };
}

/** Narrow an unknown value to a canonical ``JobStatus``, else ``null``. */
export function asJobStatus(value: unknown): JobStatus | null {
  return (JOB_STATUS_VALUES as readonly unknown[]).includes(value)
    ? (value as JobStatus)
    : null;
}

/** Narrow an unknown value to a canonical ``JobSource``, else ``null``. */
export function asJobSource(value: unknown): JobSource | null {
  return (JOB_SOURCE_VALUES as readonly unknown[]).includes(value)
    ? (value as JobSource)
    : null;
}

/** Narrow an unknown value to a ``JobsViewMode``, else ``null``. */
export function asJobsViewMode(value: unknown): JobsViewMode | null {
  return value === 'queues' || value === 'all-work' ? value : null;
}

/**
 * Normalize a raw/partial filter state into a valid ``JobsFilterState``.
 *
 * Tolerates unknown values (the URL will carry arbitrary strings in
 * Phase 5): unknown statuses are DROPPED, unknown source/view modes
 * fall back to the unset/default reading, free-form ids are trimmed
 * with empty-string collapsed to "no filter". Never throws.
 */
export function normalizeJobsFilterState(
  raw: Partial<JobsFilterState> | null | undefined,
): JobsFilterState {
  const base = createEmptyJobsFilterState();
  if (!raw) {
    return base;
  }
  const status = Array.isArray(raw.status)
    ? raw.status
        .map((s) => asJobStatus(s))
        .filter((s): s is JobStatus => s !== null)
    : base.status;
  const trimmed = (v: unknown): string | null => {
    const s = typeof v === 'string' ? v.trim() : '';
    return s.length > 0 ? s : null;
  };
  return {
    status: Array.from(new Set(status)),
    source: asJobSource(raw.source),
    agent_id: trimmed(raw.agent_id),
    project_id: trimmed(raw.project_id),
    queue_id: trimmed(raw.queue_id),
    include_deleted: raw.include_deleted === true,
    view_mode: asJobsViewMode(raw.view_mode) ?? base.view_mode,
  };
}

/**
 * Project the filter state onto the ``/api/jobs`` query shape.
 *
 * Empty values are omitted (``undefined``) so the wire never sees
 * empty tokens — the backend's ``min_length=1``-style parsers reject
 * bare params. Combos are forwarded verbatim (GATE-COMBO-FIX: the
 * settled+failed row-drop defect is BE-side arc
 * ``fix/jobs-status-combo-filter``; the FE must not compensate).
 */
export function toJobFilters(state: JobsFilterState): JobFilters {
  return {
    status: state.status.length > 0 ? state.status : undefined,
    source: state.source ?? undefined,
    agent_id: state.agent_id ?? undefined,
    project_id: state.project_id ?? undefined,
    queue_id: state.queue_id ?? undefined,
    include_deleted: state.include_deleted ? true : undefined,
  };
}

/**
 * Project the filter state onto the ``GET /api/work`` query shape.
 *
 * ``root_only`` is ALWAYS ``false`` — the "All Work" view is
 * contractually named: it must include child-instance rows, and the
 * backend default is root-scoped. ``include_deleted`` is NOT sent:
 * the work resolver has no soft-delete concept (``_query_jobs``
 * hides soft-deleted rows; there is no flag to un-hide).
 */
export function toWorkFilters(state: JobsFilterState): WorkFilters {
  return {
    status:
      state.status.length > 0 ? state.status.join(',') : undefined,
    project_id: state.project_id ?? undefined,
    root_only: false,
  };
}

/**
 * Apply the filter state to a row set — THE one filter pipeline.
 *
 * Every view mode is a projection of this function. Order-preserving
 * by construction (``filter`` never re-orders); callers must never
 * re-sort by ``created_at`` (the server's ordering is authoritative).
 *
 * View-scoped keys (honest scoping, not dead controls):
 *
 * * ``queue_id``    — queues view only. Work rows carry no queue_id
 *   (``workToJob`` pins ``null``); applying a queue filter in
 *   all-work would silently empty the list behind a hidden control.
 * * ``source``      — queues view only. The ``/api/work`` wire has no
 *   source concept (gap-e5); a source filter can only match queue
 *   rows. The control is hidden in all-work with explanatory copy.
 * * ``include_deleted`` — queues view only. ``/api/work`` has no
 *   soft-delete concept, so there is nothing to un-hide.
 *
 * ``status`` and ``agent_id`` apply in BOTH views (``Work`` carries
 * ``agent_id``) — the cross-seam invariant keys.
 */
export function applyJobsFilter(rows: Job[], state: JobsFilterState): Job[] {
  let filtered = rows;
  if (state.status.length > 0) {
    filtered = filtered.filter((job) => state.status.includes(job.status));
  }
  if (state.view_mode === 'all-work') {
    if (state.agent_id) {
      filtered = filtered.filter((job) => job.agent_id === state.agent_id);
    }
    return filtered;
  }
  if (state.source) {
    filtered = filtered.filter((job) => job.source === state.source);
  }
  if (state.agent_id) {
    filtered = filtered.filter((job) => job.agent_id === state.agent_id);
  }
  if (state.queue_id) {
    filtered = filtered.filter((job) => job.queue_id === state.queue_id);
  }
  return filtered;
}

/**
 * True when at least one USER-FACING filter is active (used for the
 * "try adjusting your filters" empty-state copy).
 *
 * ``project_id`` is intentionally excluded — it is a scoping
 * selection (which project's jobs to show), not a filter the user
 * would "clear" (it has its own UI affordance and localStorage
 * persistence). Matches the pre-P1 ``hasActiveFilters`` semantics
 * (status/source/agent_id) and adds ``queue_id`` (the pre-P1
 * component checked it; the spec mirror did not — the component was
 * right, the queue sidebar IS a filter).
 */
export function hasActiveJobsFilter(state: JobsFilterState): boolean {
  return (
    state.status.length > 0 ||
    state.source !== null ||
    state.agent_id !== null ||
    state.queue_id !== null
  );
}

/**
 * Serialize the filter state into a flat string map — the URL-ready
 * shape (Phase 5 binds this to query params; P1 ships the pure
 * round-trip so the contract is pinned before the router lands).
 *
 * Unset values are OMITTED (never empty tokens) so the map is stable
 * under parse→serialize.
 */
export function serializeJobsFilterState(
  state: JobsFilterState,
): Record<string, string> {
  const out: Record<string, string> = {};
  if (state.status.length > 0) out['status'] = state.status.join(',');
  if (state.source) out['source'] = state.source;
  if (state.agent_id) out['agent_id'] = state.agent_id;
  if (state.project_id) out['project_id'] = state.project_id;
  if (state.queue_id) out['queue_id'] = state.queue_id;
  if (state.include_deleted) out['include_deleted'] = 'true';
  out['view_mode'] = state.view_mode;
  return out;
}

/**
 * Parse a flat string map (query params) back into a valid
 * ``JobsFilterState``. Unknown values are tolerated and dropped per
 * ``normalizeJobsFilterState`` — a hand-edited URL must never throw.
 *
 * ``include_deleted`` is tolerant: ``'true'`` AND ``'1'`` both parse
 * to true (the serializer emits ``'true'``; ``'1'`` is accepted for
 * hand-edited/aliased URLs).
 */
export function parseJobsFilterState(
  raw: Record<string, string | null | undefined> | null | undefined,
): JobsFilterState {
  const base = createEmptyJobsFilterState();
  if (!raw) {
    return base;
  }
  return normalizeJobsFilterState({
    status: raw['status']
      ? raw['status']
          .split(',')
          .map((s) => asJobStatus(s.trim()))
          .filter((s): s is JobStatus => s !== null)
      : base.status,
    source: asJobSource((raw['source'] ?? '').trim()) ?? undefined,
    agent_id: raw['agent_id'] ?? undefined,
    project_id: raw['project_id'] ?? undefined,
    queue_id: raw['queue_id'] ?? undefined,
    include_deleted:
      raw['include_deleted'] === 'true' || raw['include_deleted'] === '1',
    view_mode: asJobsViewMode((raw['view_mode'] ?? '').trim()) ?? base.view_mode,
  });
}
