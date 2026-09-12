// Jobs grouping model — jobs-page-improvement arc, Phase 3.
//
// Mission-context grouping for the jobs page's flat list. Phase 2
// already projected the jobs through a ``WindowItem[]`` (rows only)
// for ``cdk-virtual-scroll``; Phase 3 adds mission/conversation
// headers and a flattened header|row projection that the same virtual
// scroll consumes — headers ARE items in the SAME list (no nested
// scrollers), so the page's render contract holds.
//
// Coalesced key (panel-proven, ``instance-node.model.ts:382-394``):
//   ``job.mission_id ?? job.instance_id ?? NO_MISSION_CONTEXT_KEY``.
//
// The fallback chain is the panel's live smoke-fix F1
// (2026-09-08): the BE jobs LIST wire ships ``mission_id: null`` for
// child-bound rows, but ``instance_id`` stays populated. When BOTH
// fields are populated they are equal by the mission identity rule
// (``mission_id == instance_id``); the fallback CANNOT re-key a job
// to a different node. When BOTH are null (a row with no mission
// context at all) the row lands in the explicit ``(no mission
// context)`` fallback group — NEVER silently dropped.
//
// NEVER-hide contract: every input row appears exactly once across
// the groups; a row with a populated mission_id AND instance_id can
// only be in the matching group (the identity rule). The model is
// pure — spec-able by direct call (see jobs-grouping.model.spec.ts).
//
// Vocabulary (ADR-MISSION-01 / M3, 2026-09-03): this module emits
// the LIVE/TERMINAL split on a group via ``isLive`` — the panel's
// vocabulary. ``settled`` is a TRANSPORT-receipt status that lives on
// the JOB, not on the MISSION; the group itself never reports a
// settled liveness.

import type { Job } from '../models/job.model';
import { isTerminalStatus } from '../models/job.model';

/**
 * The grouping key — never null. Rows with both ``mission_id`` and
 * ``instance_id`` populated collapse to one key (the identity rule);
 * rows with only ``instance_id`` fall back to that id; rows with
 * neither land in the explicit ``NO_MISSION_CONTEXT_KEY`` group.
 *
 * The string is stable: equal keys mean the SAME mission context, and
 * the FE never re-keys a row (panel :367-378 invariant). Used as the
 * ``cdk-virtual-scroll`` track-by identity for header items so DOM-
 * stable identity survives recycle.
 */
export type GroupKey = string;

/**
 * The no-context fallback group key. Exported as a NAMED constant
 * (not a magic string) so the spec can grep for the EXACT key the
 * template renders. NEVER-HIDE: a row with no mission context
 * anywhere still has to render — putting it in this group is the
 * structural answer to "what do we render?".
 */
export const NO_MISSION_CONTEXT_KEY = '__no_mission_context__';

/**
 * The no-context group's user-facing title. Pinned so the spec can
 * grep for the EXACT copy the template renders (F-5 anchor).
 *
 * M3 prose rule: "settled" belongs only to transport-receipt vocab
 * (mirror rows); the mission-side "no context" copy uses the
 * canonical liveness wording (here: "No mission context") — NOT
 * "Settled" or any settled-adjacent phrase.
 */
export const NO_MISSION_CONTEXT_TITLE = 'No mission context';

/**
 * Named constant for the lazy title-enrichment cap. The plan calls
 * this out explicitly: "capped at named constant
 * ``MAX_TITLE_ENRICHMENT_FETCHES = 3`` (spec-pinned, not a magic
 * number)". The component reads this constant when deciding how many
 * group headers to enrich; the spec pins the value so a future bump
 * is a deliberate edit, not a typo.
 *
 * Note (P3 review — approximation): the cap is APPROXIMATELY
 * "visible groups" — the picker iterates ``jobGroups()`` in
 * insertion order and stops after the cap is reached, so the actual
 * targets are the FIRST ``MAX_TITLE_ENRICHMENT_FETCHES`` eligible
 * groups in display order. Collapsed / terminal / no-context /
 * already-enriched / already-attempted groups are filtered BEFORE
 * the cap applies, so the 3 GETs may be fewer if the page has
 * many ineligible groups. The cap is also bounded by the
 * in-effect-attempted-keys tracking (the cap is checked against
 * ``attemptedKeys.size``, not per-targets.length), so the cascade
 * prevention lives here too.
 */
export const MAX_TITLE_ENRICHMENT_FETCHES = 3;

/**
 * One grouping header — the rows attached to it, the coalesced key,
 * and the meta data the header line needs.
 *
 * Fields:
 * * ``key``             — the coalesced group key (== ``mission_id
 *                          ?? instance_id ?? NO_MISSION_CONTEXT_KEY``).
 *                          Also the track-by identity for the virtual
 *                          scroll's header arm.
 * * ``missionId``       — the mission_id of the group (``null`` for
 *                          the fallback group AND for child-bound rows
 *                          where only instance_id matched).
 * * ``instanceId``      — the instance_id of the group (``null`` for
 *                          the fallback group ONLY).
 * * ``agentId``         — the representative agent (any row's
 *                          ``agent_id``; the group has no canonical
 *                          agent — the header meta line uses the
 *                          first non-null entry).
 * * ``jobCount``        — the number of rows in this group (== jobs.length).
 * * ``lastActivityAt``  — the most recent ``created_at`` across the
 *                          rows (header meta line's "timeAgo" target).
 * * ``jobs``            — the rows in server order (NEVER re-sorted
 *                          client-side; merge-order rule).
 * * ``isLive``          — true iff at least one row is non-terminal
 *                          (``pending`` / ``processing`` / ``paused``).
 *                          Drives the auto-expand seed (first 2 live
 *                          groups) and the live-first sort order.
 */
export interface JobGroup {
  readonly key: GroupKey;
  readonly missionId: string | null;
  readonly instanceId: string | null;
  readonly agentId: string | null;
  readonly jobCount: number;
  readonly lastActivityAt: string | null;
  readonly jobs: readonly Job[];
  readonly isLive: boolean;
}

/**
 * Coalesced grouping key — never null, never ambiguous. Stable across
 * re-groupings: a row with the same mission+instance fields always
 * collapses to the same key, so a filter change that regroups the
 * dataset CANNOT re-key a row to a different group (panel :367-378
 * invariant, the canonical-coalescing rule).
 *
 * Three-way precedence:
 * 1. ``mission_id`` — the canonical mission key (BE ships this on
 *    every job payload).
 * 2. ``instance_id`` — the fallback for child-bound rows that ship
 *    ``mission_id: null`` (the BE list enrichment's
 *    ``root_only=True`` default drops child-bound JobItems from the
 *    work-record enrichment, so the list wire ships null while the
 *    raw ``JobItem.instance_id`` stays populated).
 * 3. ``NO_MISSION_CONTEXT_KEY`` — neither field present; the row
 *    still has to render, so it lands in the explicit fallback group.
 *
 * @example
 *   groupingKey({ mission_id: 'm-1', instance_id: 'i-1' }) === 'm-1'
 *   groupingKey({ mission_id: null, instance_id: 'i-1' }) === 'i-1'
 *   groupingKey({ mission_id: null, instance_id: null }) === NO_MISSION_CONTEXT_KEY
 */
export function groupingKey(
  job: Pick<Job, 'mission_id' | 'instance_id'>,
): GroupKey {
  return job.mission_id ?? job.instance_id ?? NO_MISSION_CONTEXT_KEY;
}

/**
 * Pure: build groups from a flat job list. Order-preserving by
 * construction — the GROUP order is determined by the FIRST row in
 * each group (i.e. the server's ordering of the first job per key);
 * the row order WITHIN each group is the server order. Filtering
 * regroups without orphaning rows (cross-seam invariant).
 *
 * NEVER-hide: every input row appears in exactly one group; no row
 * is silently dropped or duplicated. A row's coalesced key is
 * computed once via ``groupingKey`` and the row is pushed into
 * ``groups.get(key).jobs`` (or a fresh group if the key is new). A
 * row with both mission+instance populated lands in the SAME group
 * as any other row with the same fields (identity rule).
 *
 * @param jobs flat job list, server order
 * @returns ordered, never-empty list of groups (returns ``[]`` for
 *          an empty input — callers should branch on the result).
 */
export function groupJobs(jobs: readonly Job[]): readonly JobGroup[] {
  // Stable insertion order: walk the jobs once, build groups in the
  // order their keys first appear. Map preserves insertion order
  // (ECMAScript 2015+ spec) so the group array is deterministic.
  const groupMap = new Map<GroupKey, JobGroup>();

  for (const job of jobs) {
    const key = groupingKey(job);
    const existing = groupMap.get(key);
    if (existing) {
      // Append the row; recompute the meta fields. lastActivityAt
      // picks the most recent created_at (string compare is correct
      // for ISO-8601 timestamps).
      const newLast =
        !existing.lastActivityAt ||
        (job.created_at && job.created_at > existing.lastActivityAt)
          ? job.created_at
          : existing.lastActivityAt;
      const nextJobs = [...existing.jobs, job];
      const nextIsLive = existing.isLive || !isTerminalStatus(job.status);
      const nextAgent =
        existing.agentId ??
        (typeof job.agent_id === 'string' ? job.agent_id : null);
      groupMap.set(key, {
        key: existing.key,
        missionId: existing.missionId,
        instanceId: existing.instanceId,
        agentId: nextAgent,
        jobCount: nextJobs.length,
        lastActivityAt: newLast,
        jobs: nextJobs,
        isLive: nextIsLive,
      });
    } else {
      // New key — first row seeds the meta.
      groupMap.set(key, {
        key,
        missionId: job.mission_id ?? null,
        instanceId: job.instance_id ?? null,
        agentId: typeof job.agent_id === 'string' ? job.agent_id : null,
        jobCount: 1,
        lastActivityAt: job.created_at ?? null,
        jobs: [job],
        isLive: !isTerminalStatus(job.status),
      });
    }
  }

  return Array.from(groupMap.values());
}

/**
 * Compute the auto-expand seed ids (the G1 panel port — exactly the
 * first 2 LIVE groups, terminal groups stay collapsed, the no-context
 * fallback group also starts collapsed so a misconfigured dataset
 * never pushes real work out of the auto-seed window).
 *
 * Returns an array of keys (NOT a Set) so callers can persist the
 * seed verbatim and merge it into their ``expandedIds`` Set. The
 * plan calls for "FIRST 2 live groups auto-expand" (panel :145-154
 * G1 port); terminal groups ALWAYS start collapsed (the panel's
 * terminal-root rule). The no-context fallback is a terminal-shaped
 * group (the rows in it have no mission context; per the panel's
 * rule, terminal-shaped groups start collapsed) — included here so
 * future readers don't have to re-derive the rationale.
 *
 * @example
 *   // Two live groups → auto-expand the first two.
 *   autoExpandGroupIds([{ key: 'g-1', isLive: true, ... }, { key: 'g-2', isLive: true, ... }, { key: 'g-3', isLive: false, ... }])
 *     === ['g-1', 'g-2']
 */
export function autoExpandGroupIds(
  groups: readonly JobGroup[],
): readonly GroupKey[] {
  return groups
    .filter((g) => g.isLive)
    .slice(0, 2)
    .map((g) => g.key);
}

/**
 * Live-first sort comparator for groups. Live groups (any non-terminal
 * row) come before terminal groups; ties break on the most-recent
 * activity DESC; final tiebreak on the group key ASC (deterministic).
 *
 * Matches the panel's ``compareInstanceNodes`` semantics (pinned-
 * first, activity desc, id asc) for the live/terminal split. Used
 * to project a regrouped list into the user's display order —
 * grouping itself is a projection that NEVER re-sorts the jobs
 * inside a group.
 */
export function compareJobGroups(a: JobGroup, b: JobGroup): number {
  if (a.isLive !== b.isLive) {
    return a.isLive ? -1 : 1;
  }
  const aAct = a.lastActivityAt ?? '';
  const bAct = b.lastActivityAt ?? '';
  if (aAct !== bAct) {
    return bAct.localeCompare(aAct);
  }
  return a.key.localeCompare(b.key);
}

/** Default ISO-string formatter — mirrors the panel's default
 *  ``defaultInstanceTimeAgo``. Single source for the page so the
 *  meta-line wording stays consistent. */
export function defaultGroupTimeAgo(
  dateString: string | null | undefined,
): string {
  if (!dateString) return '';
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return '';
  const diffMs = Date.now() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return 'just now';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}

/**
 * Build the meta line for a group header —
 * ``agent · N jobs · timeAgo(last activity)`` — with zero-count
 * segments dropped. Mirrors the panel's ``instanceMetaLine``
 * (panel :532-567) so a childless/jobless header reads
 * ``agent · timeAgo`` instead of ``agent · 0 jobs · timeAgo``.
 *
 * The agent segment always renders (a dash placeholder when the
 * rows carry no agent id — the spec pins this exact wording).
 *
 * @example
 *   groupMetaLine({ agentId: 'leader', jobCount: 5, lastActivityAt: '2026-09-10T10:00:00Z' })
 *     === 'leader · 5 jobs · 2h ago'
 *   groupMetaLine({ agentId: null, jobCount: 0, lastActivityAt: null })
 *     === '— · idle'
 */
export function groupMetaLine(
  group: Pick<JobGroup, 'agentId' | 'jobCount' | 'lastActivityAt'>,
  timeAgoFn?: (d: string | null | undefined) => string,
): string {
  const formatter = timeAgoFn ?? defaultGroupTimeAgo;
  const agent = group.agentId ?? '—';
  const ago =
    formatter(group.lastActivityAt) ||
    // M3 prose rule: avoid "settled" in mission-side prose. The
    // fallback "idle" reads as a neutral liveness descriptor.
    'idle';
  const parts: string[] = [agent];
  // P3 review — defensive / unreachable branch (doc-truth).
  // ``JobGroup.jobCount`` is always ``>= 1`` because the group is
  // BUILT from the rows it contains (see ``groupJobs`` — every
  // branch appends a row, so a fresh group has ``jobCount === 1``
  // and a recycled group only ever grows). The branch stays as a
  // belt-and-braces guard against a future refactor that lets a
  // group exist without rows (e.g. a manually-constructed group
  // passed in by a test or a future "empty placeholder" group); a
  // 0-count group would render as ``agent · idle`` rather than
  // ``agent · 0 jobs · idle``.
  if (group.jobCount > 0) {
    parts.push(`${group.jobCount} job${group.jobCount === 1 ? '' : 's'}`);
  }
  parts.push(ago);
  return parts.join(' · ');
}

/**
 * Header title for a group — fallback only. The plan calls for the
 * ``instanceDisplayTitle`` chain (``title → agent_id ·
 * timeAgo(created_at)``), so we synthesise a minimal InstanceRow-
 * shaped object from the group's representative agent and the most
 * recent activity. The component wires the lazy enrichment fetch
 * (``GET /api/missions/{id}``) on top — this is the FALLBACK the
 * template renders until enrichment returns.
 *
 * The NO_MISSION_CONTEXT group short-circuits to the pinned
 * ``NO_MISSION_CONTEXT_TITLE`` so the fallback copy never reads as
 * mission-side prose (the panel-port contract).
 *
 * The M3 prose rule: this function NEVER emits the word "settled".
 * ``settled`` is a transport-receipt vocabulary word; mission-side
 * fallback prose uses "idle" / "just now" / liveness-cluster
 * wording instead.
 */
export function groupHeaderTitle(
  group: Pick<JobGroup, 'key' | 'agentId' | 'lastActivityAt'>,
  timeAgoFn?: (d: string | null | undefined) => string,
): string {
  if (group.key === NO_MISSION_CONTEXT_KEY) {
    return NO_MISSION_CONTEXT_TITLE;
  }
  const formatter = timeAgoFn ?? defaultGroupTimeAgo;
  const agent = group.agentId ?? '';
  const ago = formatter(group.lastActivityAt);
  if (agent && ago) return `${agent} · ${ago}`;
  if (agent) return agent;
  if (ago) return ago;
  return '';
}