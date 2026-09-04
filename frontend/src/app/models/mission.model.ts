// Missions read-model types (Fix C §8.4 / M4-i pull-forward).
//
// FE-side counterpart of ``GET /api/missions`` (daemon/routers/missions.py,
// contract documented in docs/job-task-system.md §8.4). The badge consumes
// ONLY the live-mission count — the authoritative missions projection —
// instead of deriving it from recent job receipts.

import type { MissionLiveness } from './job.model';

/**
 * Minimal mission row — only the fields the FE reads today. The wire
 * carries more (parent_mission_id, terminal_reason, epoch, …); add
 * fields here when a consumer actually needs them, never speculatively.
 * All fields mirror the backend's nullable degraded-lookup contract
 * (§8.2: 200 with None-fields, never 500).
 */
export interface MissionSummary {
  mission_id: string | null;
  agent_id: string | null;
  liveness: MissionLiveness | null;
}

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
