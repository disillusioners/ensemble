// Jobs window model — jobs-page-improvement arc, Phase 2.
//
// The page's data window has three distinct concepts that are easy
// to conflate, so each gets a name:
//
// * WINDOW HONESTY — the BE has NO offset/cursor and NO real total
//   (``JobListResponse.total = len(page)`` on ``/api/jobs``;
//   ``/api/work`` has no pagination at all). The wire is an explicit
//   newest-100 slice. The user MUST be told when a render sits at the
//   edge of that slice — at exactly 100 rows the banner MUST show,
//   because we cannot know whether the list is complete or truncated
//   (gap-c from the planner's api-capabilities analysis). The copy is
//   therefore honest BOTH-WAYS — "may continue or may be complete".
//   Below 100, the banner stays hidden so the operator isn't nagged
//   while the projection is small enough to be obviously bounded.
//
// * RENDER GUARD — the work wire has no server-side cap, so an
//   all-work window CAN grow beyond the card-render budget. We refuse
//   to render more than ``MAX_RENDER_ROWS`` rows at render time
//   (NEVER silently slice — the user MUST see what was hidden) and
//   surface a truncation notice with the count + a "switch to
//   Queues view" affordance (Queues is the bounded window surface
//   backed by ``/api/jobs``'s clamp-100 wire). The guard fires over
//   the template-bound projected rows so growth between fetch and
//   render cannot slip through.
//
// * VIRTUAL SCROLL SOURCE — Phase 3 groups rows under mission
//   headers, so the scroll list must be a flattened
//   ``WindowItem[]`` from day 1 (``kind: 'row' | 'header'``). Phase 2
//   only ships the ``row`` arm; the header shape is reserved so the
//   Phase 3 projection is a list extension, not a rewrite.
//
// Pure module — no Angular imports, no side effects. Spec-able by
// direct call (see jobs-window.model.spec.ts).

import type { Job } from '../../models/job.model';

/** The wire's explicit newest-100 cap (``GET /api/jobs``). */
export const DEFAULT_WINDOW_LIMIT = 100;

/** The render-time hard guard for unbounded growth (all-work view). */
export const MAX_RENDER_ROWS = 1000;

/**
 * Banner state derived from the current render row count.
 *
 * * ``hidden`` — rowCount < limit; the page is obviously bounded.
 *   The banner stays out of the DOM (no reserved space).
 * * ``visible`` — rowCount >= limit; the user MUST be told the
 *   window is at its cap. At exactly ``limit`` rows we use the
 *   BOTH-WAYS copy ("may continue or may be complete") because we
 *   cannot tell which case it is.
 *
 * Both branches are pure functions of the row count — the banner is
 * driven by data, not by hand-set flags.
 */
export type WindowBannerState = 'hidden' | 'visible';

/**
 * Pure banner policy. ``rowCount`` is the projected (post-filter)
 * row count bound to the template; the banner is an exact function
 * of that count plus the limit.
 *
 * Truth table (limit = 100 default):
 *
 *   rowCount  | banner
 *   --------- | -------
 *     0-99    | hidden
 *    100+     | visible
 *
 * The plan calls out the exactly-100 edge explicitly: the banner
 * shows there because at the cap we cannot know whether the slice is
 * complete or truncated.
 */
export function windowIsFull(
  rowCount: number,
  limit: number = DEFAULT_WINDOW_LIMIT,
): WindowBannerState {
  if (rowCount >= limit) {
    return 'visible';
  }
  return 'hidden';
}

/**
 * The honesty-banner copy. Pinned here so the spec can grep for the
 * EXACT string the template renders — the banner is the user-facing
 * truth surface and its copy cannot drift silently.
 *
 * Both phrases are deliberately BOTH-WAYS at the exactly-100 edge:
 * "may continue or may be complete" — we genuinely do not know.
 * The plan explicitly rejects "Showing first 100" wording, which
 * implies an order-and-truncation contract the wire does not
 * provide.
 */
export const WINDOW_BANNER_COPY = {
  /** Banner shown when the row count is AT or ABOVE the wire cap. */
  visible:
    'Showing newest ' +
    String(DEFAULT_WINDOW_LIMIT) +
    ' · the list may continue or may be complete — refine filters to narrow',
  /** Accessible label for the manual Reload button on the banner. */
  reloadAccessibleLabel: 'Reload jobs',
} as const;

/**
 * Render-guard outcome. The component branches on the discriminator
 * so a silent slice is structurally impossible:
 *
 * * ``ok``      — every row fits; render in full.
 * * ``guarded`` — at or above the cap; render ONLY the kept slice
 *                 and surface the explicit notice. The original list
 *                 is preserved by the guard, never mutated.
 */
export type RenderGuardOutcome<T> =
  | { kind: 'ok'; rows: readonly T[] }
  | {
      kind: 'guarded';
      kept: readonly T[];
      hidden: number;
      cap: number;
      notice: string;
    };

/**
 * Render-time hard guard. Refuses to render more than
 * ``MAX_RENDER_ROWS`` rows. The output is a tagged union so callers
 * MUST branch on ``ok`` vs ``guarded`` — silent slicing is
 * structurally impossible (the type system enforces it).
 *
 * Plan task 3 acceptance:
 * * Spec fixtures AT (1000) and PAST (1001) the cap.
 * * Guard fires over the template-bound projected rows, not the raw
 *   fetch payload — growth between fetch and render cannot slip
 *   through.
 * * The notice carries a "switch to Queues view" affordance so the
 *   operator can move to the bounded window surface in one click.
 */
export function renderGuard<T>(
  rows: readonly T[],
  cap: number = MAX_RENDER_ROWS,
): RenderGuardOutcome<T> {
  if (rows.length <= cap) {
    return { kind: 'ok', rows };
  }
  const hidden = rows.length - cap;
  const notice =
    `Showing the newest ${cap} of ${rows.length} rows — ` +
    `the rest are hidden to keep the page responsive. ` +
    `Switch to the Queues view for a bounded window, or refine filters to narrow.`;
  return {
    kind: 'guarded',
    kept: rows.slice(0, cap),
    hidden,
    cap,
    notice,
  };
}

/**
 * Virtual-scroll source row. Phase 3 adds a ``kind: 'header'`` arm
 * for mission-grouped headers; Phase 2 only ships the ``row`` arm
 * (the union shape is reserved so Phase 3 is a list extension, not a
 * rewrite).
 *
 * Pure data — the component projects the
 * ``store.filteredJobs()`` output into a ``WindowItem[]`` once per
 * render tick (cheap O(n) shape change). The track-by identity is
 * always ``job_id`` so virtual-recycle never confuses rows.
 */
export type WindowItem =
  | { readonly kind: 'row'; readonly job: Job; readonly key: string }
  | {
      readonly kind: 'header';
      readonly key: string;
      readonly missionId: string;
      readonly title: string;
    };

/**
 * Project a list of Jobs into the virtual-scroll ``WindowItem[]``.
 *
 * The track key is ``job.job_id`` so ``cdkVirtualForOf`` keeps the
 * DOM-stable identity through recycle — expansion state keyed by
 * ``job_id`` in the store (NOT in the DOM) survives recycle.
 *
 * Returns a NEW array on every call (immutable); the component
 * memoizes by row-count + identity check (cheap O(n) compare) so
 * the projection does not fire on every change-detection pass.
 */
export function toWindowItems(jobs: readonly Job[]): readonly WindowItem[] {
  return jobs.map((job) => ({
    kind: 'row' as const,
    job,
    key: job.job_id,
  }));
}