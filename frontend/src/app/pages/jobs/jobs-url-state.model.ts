// Jobs URL State codec — jobs-page-improvement arc, Phase 5.
//
// The query-param ↔ filter-state shape (with the `?job=<id>` deep-link
// addition). Pre-Phase-5 nothing was shareable — only project +
// view-mode survived a reload (via localStorage). Phase 5 binds the
// filter state to query params and adds the deep-link so a bookmark
// can open the drawer directly.
//
// This is a PURE module — no Angular imports, no side effects. Every
// helper is spec-able as a plain function call (see
// ``jobs-url-state.model.spec.ts``). The codec wraps the existing
// ``serializeJobsFilterState`` / ``parseJobsFilterState`` in
// ``jobs-filter-state.model.ts`` and adds the deep-link `job` param.
//
// GATE-COMBO-FIX: this codec MUST NOT silently rewrite a
// `status=settled,failed` combo. The settled-involving combo defect
// is owned by the parallel arc `fix/jobs-status-combo-filter`; the
// combo spec is marked `gated` and skipped with reason until the
// parallel arc lands. The current behavior is to forward the combo
// verbatim to the BE.

import {
  JobsFilterState,
  JobsViewMode,
  createEmptyJobsFilterState,
  parseJobsFilterState,
  serializeJobsFilterState,
} from '../../models/jobs-filter-state.model';

/**
 * The serialized state for `/jobs` query params.
 *
 * Every key maps to a query-param key (see `serializeJobsUrlState`).
 * `job` is the deep-link handle — non-null means "open the drawer
 * for this job_id". An empty `job` is treated as no deep-link.
 */
export interface JobsUrlState {
  filter: JobsFilterState;
  /** The job_id from `?job=<id>` — null/absent means "no deep-link". */
  job: string | null;
}

/**
 * The canonical shape of the `?job=<id>` query-param value.
 *
 * Exported so the spec can pin the exact key (the plan calls it
 * `job`; it must never drift to `jobId` / `id`).
 */
export const JOB_QUERY_PARAM = 'job' as const;

/**
 * Build the default URL state — empty filter + no deep-link.
 *
 * The default view-mode is the store's default (`queues`); the
 * URL-side `serializeJobsUrlState` ALWAYS emits the `view_mode`
 * key (so a URL restore is explicit, never implicit). See the
 * spec for the round-trip property.
 */
export function createEmptyJobsUrlState(): JobsUrlState {
  return {
    filter: createEmptyJobsFilterState(),
    job: null,
  };
}

/**
 * Parse a flat string map (query params) back into a `JobsUrlState`.
 *
 * Tolerance contract (P5 plan task 1):
 *
 * * Unknown values are dropped per the filter-state parser
 *   (`parseJobsFilterState` — itself a tolerant parser).
 * * `?job=` (empty string) collapses to `null` (no deep-link).
 * * Unknown `?job=<garbage>` is PRESERVED (the drawer open path
 *   will surface an honest "job not found" state on the GET 404).
 *   A hostile URL must NOT throw.
 * * Duplicated params are tolerated — the first non-empty wins
 *   (Angular's `queryParamMap` already collapses duplicates by
 *   the last-wins rule; the spec pins the codec's behavior).
 * * Status combos are forwarded verbatim — see GATE-COMBO-FIX.
 * * ``include_deleted`` is tolerant: ``'true'`` AND ``'1'`` both
 *   parse to true (hand-edited URLs must not silently drop the
 *   flag; see ``parseJobsFilterState``).
 */
export function parseJobsUrlState(
  raw: Record<string, string | string[] | null | undefined> | null | undefined,
): JobsUrlState {
  const base = createEmptyJobsUrlState();
  if (!raw) {
    return base;
  }
  // Coerce `string[]` (Angular's repeat-key form) to the first
  // scalar value — the codec is a single-value map; the page-side
  // hostile-input fixture exercises this branch.
  const flatten = (
    value: string | string[] | null | undefined,
  ): string | null => {
    if (value === null || value === undefined) return null;
    if (Array.isArray(value)) {
      // First non-empty wins; the spec exercises the multi-key form.
      for (const v of value) {
        if (typeof v === 'string' && v.length > 0) return v;
      }
      return null;
    }
    return value;
  };
  const filterRaw: Record<string, string | null | undefined> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (k === JOB_QUERY_PARAM) continue;
    const flat = flatten(v);
    filterRaw[k] = flat === null ? undefined : flat;
  }
  const jobRaw = flatten(raw[JOB_QUERY_PARAM]);
  const job = jobRaw && jobRaw.trim().length > 0 ? jobRaw.trim() : null;
  return {
    filter: parseJobsFilterState(filterRaw),
    job,
  };
}

// ── P5 rev — the one-time bare-URL migration decision (REAL logic) ────
//
// This is the SEED mechanism behind the view-mode/project localStorage
// migration, extracted as a pure function so the spec drives the REAL
// production code (the pre-rev spec tests were tautological: a
// spec-local ``migrate`` that set a flag and asserted the flag — they
// could not catch the dead-seed regression where the seed became
// unreachable in the component).

/** What the migration decided for this URL emit. */
export type JobsUrlMigrationAction =
  /** Apply ``patch`` to the store, then arm the URL cycle guard. */
  | 'seed'
  /** The URL carries an explicit view/job param — the URL is authoritative. */
  | 'url-wins'
  /** Nothing to do (already migrated, or the seed would be a no-op). */
  | 'none';

export interface JobsUrlMigrationInput {
  /** The RAW query-param map (the parsed shape always has a view_mode). */
  rawParams: Record<string, string | string[] | null | undefined> | null | undefined;
  /** One-time guard — true once the migration has completed. */
  migrationDone: boolean;
  /** Legacy ``job-page-selected-project`` value (null when unset/unreadable). */
  savedProjectId: string | null;
  /** Legacy ``job-page-view-mode`` value (null when unset/unreadable). */
  savedViewMode: string | null;
  /** project_ids that exist right now (a stale saved id is dropped). */
  knownProjectIds: readonly string[];
  /** The PARSED URL filter — the seed merges INTO it (URL params survive). */
  urlFilter: JobsFilterState;
}

export interface JobsUrlMigrationResult {
  action: JobsUrlMigrationAction;
  /** The store patch — non-null ONLY for ``action: 'seed'``. */
  patch: Partial<JobsFilterState> | null;
}

/** Literal param presence on the RAW map (parsed values never answer this). */
function rawParamPresent(
  value: string | string[] | null | undefined,
): boolean {
  if (value === null || value === undefined) return false;
  if (Array.isArray(value)) {
    return value.some((v) => typeof v === 'string' && v.length > 0);
  }
  return value.length > 0;
}

/**
 * Decide the one-time localStorage seed for a boot URL.
 *
 * * ``migrationDone`` → ``'none'`` (never re-seed on a second load).
 * * A ``view_mode`` or ``job`` param LITERALLY present on the raw map
 *   → ``'url-wins'`` (the URL is authoritative; the caller clears the
 *   legacy keys — the one-time cleanup). Note the PARSED state cannot
 *   answer this: ``parseJobsFilterState`` never yields a null
 *   ``view_mode``, which is exactly why the pre-rev seed was dead.
 * * Otherwise (job/view params literally absent — an empty-string
 *   param counts as absent) → ``'seed'`` with a patch that merges the
 *   legacy keys INTO the parsed URL filter, so URL params like
 *   ``status`` survive the seed. An explicit ``project_id`` param
 *   beats the saved project; a saved project is dropped when it no
 *   longer exists.
 */
export function resolveJobsUrlMigration(
  input: JobsUrlMigrationInput,
): JobsUrlMigrationResult {
  if (input.migrationDone) {
    return { action: 'none', patch: null };
  }
  const raw = input.rawParams ?? {};
  const urlPinsSeedRelevantParams =
    rawParamPresent(raw['view_mode']) ||
    rawParamPresent(raw[JOB_QUERY_PARAM]);
  if (urlPinsSeedRelevantParams) {
    return { action: 'url-wins', patch: null };
  }
  const patch: Partial<JobsFilterState> = { ...input.urlFilter };
  let seeded = false;
  const savedProjectApplies =
    !!input.savedProjectId &&
    input.urlFilter.project_id === null && // an explicit URL project wins
    input.knownProjectIds.includes(input.savedProjectId);
  if (savedProjectApplies) {
    patch.project_id = input.savedProjectId;
    seeded = true;
  }
  const savedViewMode: JobsViewMode | null =
    input.savedViewMode === 'queues' || input.savedViewMode === 'all-work'
      ? input.savedViewMode
      : null;
  if (savedViewMode && savedViewMode !== input.urlFilter.view_mode) {
    patch.view_mode = savedViewMode;
    seeded = true;
  }
  if (!seeded) {
    return { action: 'none', patch: null };
  }
  return { action: 'seed', patch };
}

/**
 * Serialize the URL state into a flat string map (query params).
 *
 * Unset values are OMITTED — the codec is stable under
 * parse→serialize. ``view_mode`` is emitted when a FULL state is
 * serialized — but the PAGE's writes are DIFF-DRIVEN
 * (``diffJobsUrlState`` emits only the changed keys), so a URL
 * without ``view_mode`` (a bare URL, or a shared partial URL) is a
 * VALID wire state: parsing falls back to the store default
 * (``queues``). The URL is therefore NOT "always explicit" about the
 * active projection — the earlier docstring overclaimed.
 */
export function serializeJobsUrlState(
  state: JobsUrlState,
): Record<string, string> {
  const out = serializeJobsFilterState(state.filter);
  if (state.job && state.job.length > 0) {
    out[JOB_QUERY_PARAM] = state.job;
  }
  return out;
}

/**
 * Diff two `JobsUrlState`s — returns the keys whose values differ.
 *
 * Used by the router-write path (P5 task 2) so a filter mutation
 * generates ONLY the minimal `queryParams` patch — never a
 * full-replace. Empty result means "no URL write needed".
 *
 * `job` is included in the diff so toggling a deep-link from the
 * URL also reaches the page (one-way: URL → page) and the
 * page→URL write never blindly emits the active `job` value
 * unless the diff picks it up.
 */
export function diffJobsUrlState(
  prev: JobsUrlState,
  next: JobsUrlState,
): Record<string, string | null> {
  const prevSerialized = serializeJobsUrlState(prev);
  const nextSerialized = serializeJobsUrlState(next);
  const out: Record<string, string | null> = {};
  // Only the keys present in `next` (a serialized diff is add/remove
  // semantics — the page writes only what changed). Removed keys
  // are emitted as `null` so `router.navigate` strips them.
  const keys = new Set([
    ...Object.keys(prevSerialized),
    ...Object.keys(nextSerialized),
  ]);
  for (const k of keys) {
    const a = prevSerialized[k];
    const b = nextSerialized[k];
    if (a === b) continue;
    out[k] = b === undefined ? null : b;
  }
  return out;
}
