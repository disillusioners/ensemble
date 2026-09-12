// P3 (jobs-page-improvement) — title-enrichment target picker.
//
// Pure helper for the lazy title-enrichment effect on the Jobs page.
// Extracted out of ``JobsComponent`` so the cascade / no-retry /
// in-flight dedup / child-bound filter logic is unit-testable as a
// plain-TS function (the bug class the P3 review surfaced: a single
// source-pin passing while the production effect cascades and
// re-fetches forever). The component wires the orchestration
// (effect + subscribe + set mutations) on top — this module owns
// the target-selection policy.
//
// Spec contract (pinned by ``jobs-enrichment.model.spec.ts``):
//
// * A key is picked AT MOST ONCE per dataset generation. The
//   ``attemptedKeys`` set prevents cascade re-fires (the bug:
//   the legacy effect self-triggered on its own ``titleOverrides``
//   writes and topped up to N*3 fetches across N effect runs).
// * A key is picked AT MOST ONCE while in flight. The
//   ``inFlightKeys`` set dedups concurrent fetches for the same
//   key (the bug: no dedup → duplicate GETs for the same key when
//   the cascade ran in parallel).
// * A key that previously FAILED is never re-picked within the
//   page lifetime. The ``failedKeys`` set stops infinite retry
//   across polls (the bug: failed GETs recorded no override → the
//   group stayed eligible → re-fetched every poll forever).
// * Child-bound groups (``missionId == null``) are NEVER picked.
//   Their coalesced key matches ``instance_id`` — fetching
//   ``GET /api/missions/{instance_id}`` is semantically wrong
//   (the missions endpoint takes a mission_id, not an instance_id).
//   Their titles come from the fallback chain
//   (``groupHeaderTitle`` → ``instanceDisplayTitle`` chain).
//
// Cap (P3 review — approximation): the returned list is bounded
// ONLY by the ``cap`` parameter, which the component sets to
// ``MAX_TITLE_ENRICHMENT_FETCHES``. The component also checks
// ``attemptedKeys.size >= MAX_TITLE_ENRICHMENT_FETCHES`` BEFORE
// firing each request, so the cap is enforced at both selection
// AND at the per-iteration fire step — a defence-in-depth against
// cascade re-fires even if ``pickEnrichmentTargets`` is called
// more than once in the same cascade.

import type { JobGroup } from './jobs-grouping.model';
import { NO_MISSION_CONTEXT_KEY } from './jobs-grouping.model';

/**
 * Pure picker: which group keys should we fetch this iteration?
 *
 * Iterates the groups in display order, returns the first
 * eligible keys (those NOT filtered by any of the no-go sets).
 * The caller is responsible for honouring the cap before
 * actually firing the HTTP requests — the picker returns the
 * full candidate list (capped at ``cap``) so the caller can
 * make its own cap decisions and so a single picker call is
 * deterministic.
 *
 * Eligibility (in priority order):
 *
 * 1. NOT the no-context fallback key (``groupHeaderTitle``
 *    short-circuits there — never needs enrichment).
 * 2. Has a populated ``missionId`` (child-bound groups fall
 *    back to the ``instanceDisplayTitle`` chain; a
 *    ``GET /api/missions/{instance_id}`` would be wrong).
 * 3. NOT already overridden (already-enriched groups skip —
 *    ``titleOverrides`` map is the success-side state).
 * 4. NOT in ``attemptedKeys`` (cascade prevention — a key
 *    attempted in this dataset generation is never returned
 *    again).
 * 5. NOT in ``inFlightKeys`` (concurrent-dedup — the component
 *    mutates this set on subscribe/next/error so a second
 *    picker call during an in-flight request sees the dedup).
 * 6. NOT in ``failedKeys`` (no-retry — failed keys persist for
 *    the page lifetime; the picker will never re-emit them).
 *
 * @param groups            the groups to pick from (typically
 *                          ``jobGroups()`` in display order).
 * @param attemptedKeys     keys already attempted in this
 *                          dataset generation (cascade dedup).
 * @param inFlightKeys      keys currently being fetched
 *                          (concurrent dedup).
 * @param failedKeys        keys that previously failed (no
 *                          retry within page lifetime).
 * @param titleOverrides    the success-side map; keys with an
 *                          entry are already enriched.
 * @param cap               the cap (typically
 *                          ``MAX_TITLE_ENRICHMENT_FETCHES``);
 *                          the picker returns AT MOST ``cap``
 *                          keys, regardless of eligibility.
 * @returns                 the eligible keys in display order,
 *                          capped at ``cap``.
 */
export function pickEnrichmentTargets(
  groups: ReadonlyArray<JobGroup>,
  attemptedKeys: ReadonlySet<string>,
  inFlightKeys: ReadonlySet<string>,
  failedKeys: ReadonlySet<string>,
  titleOverrides: ReadonlyMap<string, string>,
  cap: number,
): readonly string[] {
  const targets: string[] = [];
  for (const g of groups) {
    if (g.key === NO_MISSION_CONTEXT_KEY) continue;
    // Child-bound (missionId null) groups never fetch — their
    // coalesced key is the instance_id, and ``GET /api/missions/
    // {instance_id}`` is semantically wrong (the missions
    // endpoint takes a mission_id). Their titles fall through
    // to the ``groupHeaderTitle`` chain. The mission_id field
    // is the authoritative "can we enrich this?" gate, not the
    // coalesced key.
    if (!g.missionId) continue;
    // Already enriched — skip.
    if (titleOverrides.has(g.key)) continue;
    // Already attempted in this dataset generation (cascade
    // prevention).
    if (attemptedKeys.has(g.key)) continue;
    // In-flight right now (concurrent dedup).
    if (inFlightKeys.has(g.key)) continue;
    // Previously failed (no retry within page lifetime).
    if (failedKeys.has(g.key)) continue;
    targets.push(g.key);
    if (targets.length >= cap) break;
  }
  return targets;
}
