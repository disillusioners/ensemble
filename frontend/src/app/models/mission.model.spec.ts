import { MissionListResponse, missionCountFromListResponse } from './mission.model';

/**
 * Logic-mirror spec for the missions list response count helper —
 * the badge's authoritative live-mission N (Change 1: the badge
 * counts from the missions projection, never from recent job
 * receipts).
 */
describe('mission.model — missionCountFromListResponse', () => {
  const baseResponse = (overrides?: Partial<MissionListResponse>): MissionListResponse => ({
    missions: [],
    total: null,
    limit: 1,
    offset: 0,
    has_more: null,
    degraded: false,
    ...(overrides ?? {}),
  });

  it('returns the filter-aware total when present (authoritative count with limit=1)', () => {
    const response = baseResponse({
      missions: [{ mission_id: 'm-1', agent_id: 'leader', liveness: 'processing' }],
      total: 7,
    });
    // Page holds ONE row (limit=1) but the count is the full filter match.
    expect(missionCountFromListResponse(response)).toBe(7);
  });

  it('falls back to items length when total is absent on a non-degraded page', () => {
    const response = baseResponse({
      missions: [
        { mission_id: 'm-1', agent_id: 'a', liveness: 'processing' },
        { mission_id: 'm-2', agent_id: 'b', liveness: 'paused' },
      ],
      total: null,
    });
    expect(missionCountFromListResponse(response)).toBe(2);
  });

  it('returns null on a degraded page — count unavailable must NOT read as 0', () => {
    const response = baseResponse({ missions: [], total: null, degraded: true });
    expect(missionCountFromListResponse(response)).toBeNull();
  });

  it('returns 0 for a legitimately empty, non-degraded page', () => {
    const response = baseResponse({ missions: [], total: 0, has_more: false });
    expect(missionCountFromListResponse(response)).toBe(0);
  });

  it('treats total=0 as authoritative over a non-empty fallback list', () => {
    const response = baseResponse({
      missions: [{ mission_id: 'm-1', agent_id: 'a', liveness: 'pending' }],
      total: 0,
    });
    expect(missionCountFromListResponse(response)).toBe(0);
  });
});
