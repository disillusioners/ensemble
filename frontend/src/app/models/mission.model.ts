// Missions read-model types (Fix C §8.4 / M4-i pull-forward / 2026-09-07 mission-tree panel).
//
// FE-side counterpart of ``GET /api/missions`` (daemon/routers/missions.py,
// contract documented in docs/job-task-system.md §8.4). The badge consumes
// ONLY the live-mission count — the authoritative missions projection —
// instead of deriving it from recent job receipts.

import type { MissionLiveness, MissionSummary } from './job.model';

/**
 * Canonical mission row — mirrors the BE ``MissionResponse`` wire
 * shape exactly (see ``daemon/routers/schemas.py`` class
 * ``MissionResponse``). The full type lives in ``models/job.model.ts``
 * (per the mission-tree panel brief, 2026-09-07) and is re-exported
 * here as ``MissionSummary`` for convenience so the badge / panel
 * specs share one identifier.
 *
 * Every nullable field mirrors the BE degraded-lookup contract
 * (§8.2: 200 with None-fields, never 500). The FE never invents a
 * value for a null field — the tree builder routes null-bearing
 * missions into the fallback path ("NEVER hide a job").
 */
export type { MissionSummary };

/**
 * Envelope for ``GET /api/missions``. ``total``/``has_more`` are
 * nullable by contract: ``null`` means "count leg degraded — count
 * unavailable", which must NOT be rendered as 0/false.
 */
export interface MissionListResponse {
  missions: MissionSummary[];
  total: number | null;
  limit: number;
  offset: number;
  has_more: boolean | null;
  degraded: boolean;
}

/**
 * Live-mission count from a missions list response — the badge's N.
 *
 * ``total`` is the exact filter-aware COUNT and is authoritative when
 * present; ``missions.length`` is only a defensive fallback for a
 * well-formed-but-total-less page. A degraded page (empty rows +
 * null total) returns ``null`` — "count unavailable", deliberately
 * NOT 0, so callers can retain their last known count instead of
 * falsely reading the system as idle.
 */
export function missionCountFromListResponse(
  response: MissionListResponse
): number | null {
  if (response.degraded) {
    return null;
  }
  return response.total ?? response.missions.length;
}
