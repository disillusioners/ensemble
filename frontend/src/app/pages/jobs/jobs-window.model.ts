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
 * Virtual-scroll source row. Phase 3 ships BOTH arms of the union:
 *
 * * ``row``    — a single Job row from the grouped projection
 *                (mapped under a header).
 * * ``header`` — a grouping header (mission/conversation context)
 *                from ``groupJobs``. Phase 3 ships the full shape
 *                so the template can render the chevron, title, and
 *                meta line without a separate signal.
 *
 * Both arms carry ``key`` so ``cdk-virtual-scroll`` can track-by a
 * stable identity:
 *
 * * rows:    ``job.job_id`` (recycle-safe).
 * * headers: the group key (``mission_id ?? instance_id ??
 *            NO_MISSION_CONTEXT_KEY``) — stable across regroupings
 *            (panel :367-378 invariant).
 *
 * The union is discriminated by ``kind``; the template's
 * ``@if (item.kind === 'row')`` branch renders the card and the
 * ``@else`` branch renders the header — no overlap, silent slicing
 * is structurally impossible.
 */
export type WindowItem =
  | { readonly kind: 'row'; readonly job: Job; readonly key: string }
  | {
      readonly kind: 'header';
      readonly key: string;
      readonly groupKey: string;
      readonly title: string;
      readonly metaLine: string;
      readonly isLive: boolean;
      readonly jobCount: number;
    };

/**
 * Project a flat job list into a flattened ``WindowItem[]`` —
 * header rows for each group, with the group's rows nested
 * underneath, and collapsed groups' rows OMITTED.
 *
 * Rules:
 *
 * * Groups are returned in the order ``groupJobs`` produced them
 *   (first-row-position; server-order preserved).
 * * Header rows are ALWAYS included — collapsed groups still show
 *   their header (the chevron is the only expand toggle).
 * * Rows under a COLLAPSED group are OMITTED — keyboard order ==
 *   DOM order (no off-screen rows for arrow keys to skip).
 * * Each row's ``key`` is ``job.job_id`` (recycle-safe).
 * * Each header's ``key`` is the group key (recycle-safe; same key
 *   identity as ``expandedGroupIds.has(key)``).
 *
 * The ``title`` is the fallback from ``groupHeaderTitle``; the
 * component wires the lazy ``GET /api/missions/{id}`` enrichment on
 * top (see ``MissionService.getMission``).
 *
 * The ``metaLine`` is built by ``groupMetaLine`` and is what the
 * template renders under the header title.
 */
export function toWindowItems(
  groups: ReadonlyArray<{
    readonly key: string;
    readonly missionId: string | null;
    readonly instanceId: string | null;
    readonly agentId: string | null;
    readonly jobCount: number;
    readonly lastActivityAt: string | null;
    readonly jobs: readonly Job[];
    readonly isLive: boolean;
  }>,
  expandedGroupIds: ReadonlySet<string>,
  titleFor: (group: {
    readonly key: string;
    readonly agentId: string | null;
    readonly lastActivityAt: string | null;
  }) => string,
  metaLineFor: (group: {
    readonly agentId: string | null;
    readonly jobCount: number;
    readonly lastActivityAt: string | null;
  }) => string,
): readonly WindowItem[] {
  const items: WindowItem[] = [];
  for (const group of groups) {
    items.push({
      kind: 'header',
      key: group.key,
      groupKey: group.key,
      title: titleFor(group),
      metaLine: metaLineFor(group),
      isLive: group.isLive,
      jobCount: group.jobCount,
    });
    if (expandedGroupIds.has(group.key)) {
      for (const job of group.jobs) {
        items.push({ kind: 'row', job, key: job.job_id });
      }
    }
  }
  return items;
}

/**
 * Bounded projection — produces a list of items at or under the cap,
 * slicing on GROUP boundaries (whole groups fit or overflow together).
 *
 * Why a separate function (not a slice inside ``toWindowItems``):
 * the projection and the cap are orthogonal concerns. The pure
 * ``toWindowItems`` projection stays spec-clean (every group
 * contributes its full set of items, no truncation logic). The
 * cap is a separate concern that needs to KNOW about groups to do
 * boundary slicing — it cannot be expressed as a flat-row slice
 * without producing orphan headers.
 *
 * Boundary slicing rules:
 *
 * * Walk groups in the order ``groupJobs`` produced them.
 * * For each group, its item cost is: ``1 header + (expanded ? N
 *   rows : 0)``.
 * * If the group fits in the remaining cap, include ALL its items.
 * * Else:
 *   * For an EXPANDED group that doesn't fit: omit the entire
 *     group (no header, no rows). The orphan-header case (header
 *     rendered + zero rows) would mislead the user (a header
 *     advertising "N jobs" with no jobs visible). The group's
 *     rows count toward the hidden total.
 *   * For a COLLAPSED group that doesn't fit: omit the header too.
 *     (A collapsed group is a 1-item group; the cap can still be
 *     reached if there are many collapsed groups. Headerless
 *     collapse means the user sees a truncation banner with the
 *     correct hidden-row count, which is zero for collapsed-only
 *     overflow but never lies about what was rendered.)
 *
 * Hidden count semantics:
 *
 * * Only ROWS dropped BY THE CAP count toward ``hidden``. Collapsed
 *   group's rows are NOT in the rendered list by user choice
 *   (chevron state), NOT by the cap — they are NOT counted as
 *   hidden. A user-collapsed group is a deliberate view choice,
 *   not truncation.
 * * Headers are NEVER counted toward hidden. The user-visible copy
 *   stays truthful: "N rows hidden", never "N groups hidden".
 *
 * NEVER-hide: every input row is either rendered (its group was
 * fully included) or counted in ``hidden`` (its expanded group was
 * cut by the cap). A row in a collapsed group is neither rendered
 * nor counted hidden — that's correct (collapsed = by user choice).
 *
 * The notice copy mirrors ``renderGuard``'s "newest N of M" wording
 * so the banner stays consistent across the two guards (legacy flat
 * rows vs new grouped projection).
 *
 * Returns the SAME ``RenderGuardOutcome`` discriminated union so
 * the component's existing ``kind === 'ok' | 'guarded'`` branch
 * keeps working without a typed change at the call site.
 */
export function toBoundedWindowItems(
  groups: ReadonlyArray<{
    readonly key: string;
    readonly missionId: string | null;
    readonly instanceId: string | null;
    readonly agentId: string | null;
    readonly jobCount: number;
    readonly lastActivityAt: string | null;
    readonly jobs: readonly Job[];
    readonly isLive: boolean;
  }>,
  expandedGroupIds: ReadonlySet<string>,
  titleFor: (group: {
    readonly key: string;
    readonly agentId: string | null;
    readonly lastActivityAt: string | null;
  }) => string,
  metaLineFor: (group: {
    readonly agentId: string | null;
    readonly jobCount: number;
    readonly lastActivityAt: string | null;
  }) => string,
  cap: number = MAX_RENDER_ROWS,
): RenderGuardOutcome<WindowItem> {
  const items: WindowItem[] = [];
  let totalRows = 0;
  let hidden = 0;
  let datasetTotalRows = 0;
  let hitCap = false;
  for (const group of groups) {
    datasetTotalRows += group.jobs.length;
    const isExpanded = expandedGroupIds.has(group.key);
    const rowsInGroup = isExpanded ? group.jobs.length : 0;
    const groupCost = 1 + rowsInGroup;
    if (items.length + groupCost <= cap) {
      items.push({
        kind: 'header',
        key: group.key,
        groupKey: group.key,
        title: titleFor(group),
        metaLine: metaLineFor(group),
        isLive: group.isLive,
        jobCount: group.jobCount,
      });
      totalRows += rowsInGroup;
      if (isExpanded) {
        for (const job of group.jobs) {
          items.push({ kind: 'row', job, key: job.job_id });
        }
      }
    } else if (rowsInGroup > 0) {
      // EXPANDED group didn't fit — omit the whole group (no
      // orphan header). All of its rows count toward hidden.
      hidden += rowsInGroup;
      hitCap = true;
    } else {
      // COLLAPSED group didn't fit (header-only). The header is
      // omitted too: a header without its rows renders, but the
      // cap still has to give somewhere; the user-visible effect
      // is "scroll past this group, see the truncation banner".
      // Zero rows to add to hidden — the rows weren't going to
      // render anyway.
      hitCap = true;
    }
  }
  if (!hitCap) {
    return { kind: 'ok', rows: items };
  }
  // Notice copy stays truthful against the dataset: M is the
  // total rows the page knows about (sum of every group's jobs),
  // not the rendered count. Collapsed rows are part of the dataset
  // (the user can expand to see them) but the "X hidden rows"
  // figure beside the title is the cap-dropped count only — the
  // template binds ``hidden`` to that label.
  const notice =
    `Showing the newest ${totalRows} of ${datasetTotalRows} rows — ` +
    `the rest are hidden to keep the page responsive. ` +
    `Switch to the Queues view for a bounded window, or refine filters to narrow.`;
  return {
    kind: 'guarded',
    kept: items,
    hidden,
    cap,
    notice,
  };
}