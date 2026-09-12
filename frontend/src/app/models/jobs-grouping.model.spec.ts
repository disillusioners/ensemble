// jobs-grouping.model spec — jobs-page-improvement arc, Phase 3.
//
// Plain-TS specs over the pure grouping model. Pins:
//
// * groupingKey three-way precedence (mission_id > instance_id >
//   NO_MISSION_CONTEXT_KEY); the fallback cannot re-key a row.
// * groupJobs property: every input row appears EXACTLY once across
//   groups (never-hide), with MULTI-GROUP fixtures mixing live,
//   terminal, and no-context rows (the plan's flagship invariant).
// * groupJobs preserves server order WITHIN each group and uses the
//   FIRST row's position for the group ORDER (insertion order).
// * groupJobs recycles the agent_id and lastActivityAt correctly
//   across multiple rows (first non-null agent; most-recent activity).
// * autoExpandGroupIds pins the FIRST-2-LIVE-ONLY seed (terminal
//   groups start collapsed; no-context group also starts collapsed).
// * compareJobGroups: live first, activity desc, key asc tiebreak.
// * groupMetaLine: agent always renders, zero-count segments drop,
//   placeholder falls back to "idle" (NOT "settled" — M3 prose rule).
// * groupHeaderTitle: instanceDisplayTitle port, never emits
//   "settled" anywhere (M3 prose rule); no-context group short-
//   circuits to the pinned NO_MISSION_CONTEXT_TITLE.
// * MAX_TITLE_ENRICHMENT_FETCHES = 3 (named constant pin — spec
//   asserts the value so a future bump is a deliberate edit).

import {
  MAX_TITLE_ENRICHMENT_FETCHES,
  NO_MISSION_CONTEXT_KEY,
  NO_MISSION_CONTEXT_TITLE,
  autoExpandGroupIds,
  compareJobGroups,
  defaultGroupTimeAgo,
  groupHeaderTitle,
  groupJobs,
  groupMetaLine,
  groupingKey,
} from './jobs-grouping.model';
import { createMockJob } from '../testing/job-test-helpers';

describe('jobs-grouping — MAX_TITLE_ENRICHMENT_FETCHES named constant', () => {
  it('is pinned at 3 (the plan\'s named-constant rule, not a magic number)', () => {
    expect(MAX_TITLE_ENRICHMENT_FETCHES).toBe(3);
  });
});

describe('jobs-grouping — NO_MISSION_CONTEXT_KEY pinned string', () => {
  it('is a non-empty, non-null sentinel value (the plan\'s never-hide guarantee)', () => {
    expect(typeof NO_MISSION_CONTEXT_KEY).toBe('string');
    expect(NO_MISSION_CONTEXT_KEY.length).toBeGreaterThan(0);
  });

  it('NO_MISSION_CONTEXT_TITLE is pinned (template F-5 anchor)', () => {
    expect(NO_MISSION_CONTEXT_TITLE).toBe('No mission context');
  });
});

describe('jobs-grouping — groupingKey coalesced-key rule', () => {
  it('mission_id wins when both fields are populated (identity rule: mission_id == instance_id)', () => {
    expect(
      groupingKey({ mission_id: 'm-1', instance_id: 'i-1' }),
    ).toBe('m-1');
  });

  it('instance_id is the fallback for child-bound rows (mission_id: null on the list wire)', () => {
    expect(
      groupingKey({ mission_id: null, instance_id: 'i-1' }),
    ).toBe('i-1');
  });

  it('NO_MISSION_CONTEXT_KEY is the fallback for rows with NEITHER field (never-hide)', () => {
    expect(
      groupingKey({ mission_id: null, instance_id: null }),
    ).toBe(NO_MISSION_CONTEXT_KEY);
    expect(
      groupingKey({ mission_id: undefined, instance_id: undefined }),
    ).toBe(NO_MISSION_CONTEXT_KEY);
  });

  it('the fallback cannot re-key a row to a different group (panel invariant)', () => {
    // Two rows with the SAME mission_id collapse to the same key,
    // regardless of instance_id. The coalescing rule is total.
    const a = groupingKey({ mission_id: 'm-9', instance_id: 'i-1' });
    const b = groupingKey({ mission_id: 'm-9', instance_id: 'i-2' });
    expect(a).toBe(b);
    expect(a).toBe('m-9');
  });
});

describe('jobs-grouping — groupJobs property: every row appears exactly once', () => {
  it('MULTI-GROUP fixture: 3+ groups mixing live / terminal / no-context rows — total rows == sum of group jobCount', () => {
    // The plan's flagship pin: never single-group-only, must mix
    // liveness AND include the no-context fallback. 9 input rows
    // across 4 groups, mixing pending/processing/settled/completed/
    // failed/no-context so the liveness split is non-trivial.
    const jobs = [
      // group A: m-live-1 — two live + one settled receipt
      createMockJob({
        job_id: 'a-1',
        mission_id: 'm-live-1',
        instance_id: 'i-live-1',
        agent_id: 'leader',
        status: 'pending',
        created_at: '2026-09-10T10:00:00Z',
      }),
      createMockJob({
        job_id: 'a-2',
        mission_id: 'm-live-1',
        instance_id: 'i-live-1',
        agent_id: 'leader',
        status: 'processing',
        created_at: '2026-09-10T10:05:00Z',
      }),
      createMockJob({
        job_id: 'a-3',
        mission_id: 'm-live-1',
        instance_id: 'i-live-1',
        agent_id: 'leader',
        status: 'settled',
        created_at: '2026-09-10T10:10:00Z',
      }),
      // group B: i-child-only — child-bound, mission_id null (list-wire shape)
      createMockJob({
        job_id: 'b-1',
        mission_id: null,
        instance_id: 'i-child-only',
        agent_id: 'developer',
        status: 'completed',
        created_at: '2026-09-10T09:00:00Z',
      }),
      // group C: m-terminal — fully terminal
      createMockJob({
        job_id: 'c-1',
        mission_id: 'm-terminal',
        instance_id: 'i-terminal',
        agent_id: 'tester',
        status: 'failed',
        created_at: '2026-09-09T10:00:00Z',
      }),
      createMockJob({
        job_id: 'c-2',
        mission_id: 'm-terminal',
        instance_id: 'i-terminal',
        agent_id: 'tester',
        status: 'completed',
        created_at: '2026-09-09T11:00:00Z',
      }),
      // group D: no-mission-context — both fields null
      createMockJob({
        job_id: 'd-1',
        mission_id: null,
        instance_id: null,
        agent_id: 'developer',
        status: 'pending',
        created_at: '2026-09-10T11:00:00Z',
      }),
      createMockJob({
        job_id: 'd-2',
        mission_id: null,
        instance_id: null,
        agent_id: 'developer',
        status: 'pending',
        created_at: '2026-09-10T11:30:00Z',
      }),
      createMockJob({
        job_id: 'd-3',
        mission_id: null,
        instance_id: null,
        agent_id: null as unknown as string,
        status: 'failed',
        created_at: '2026-09-09T09:00:00Z',
      }),
    ];

    const groups = groupJobs(jobs);

    // 4 distinct groups — one per coalesced key.
    expect(groups.length).toBe(4);
    // NEVER-HIDE: every input row appears EXACTLY once. Sum of
    // group jobCount == input length.
    const totalAcrossGroups = groups.reduce(
      (sum, g) => sum + g.jobCount,
      0,
    );
    expect(totalAcrossGroups).toBe(jobs.length);
    // AND every input job_id is present somewhere in the output.
    const allJobIds = new Set<string>();
    for (const g of groups) {
      for (const j of g.jobs) allJobIds.add(j.job_id);
    }
    for (const j of jobs) {
      expect(allJobIds.has(j.job_id)).toBe(true);
    }

    // group A: live + 3 rows
    const a = groups.find((g) => g.key === 'm-live-1');
    expect(a).toBeDefined();
    expect(a!.jobCount).toBe(3);
    expect(a!.isLive).toBe(true);
    expect(a!.missionId).toBe('m-live-1');
    expect(a!.instanceId).toBe('i-live-1');

    // group B: terminal-only + child-bound shape (mission_id null)
    const b = groups.find((g) => g.key === 'i-child-only');
    expect(b).toBeDefined();
    expect(b!.jobCount).toBe(1);
    expect(b!.isLive).toBe(false);
    expect(b!.missionId).toBeNull();
    expect(b!.instanceId).toBe('i-child-only');

    // group C: terminal-only
    const c = groups.find((g) => g.key === 'm-terminal');
    expect(c).toBeDefined();
    expect(c!.jobCount).toBe(2);
    expect(c!.isLive).toBe(false);

    // group D: no-context fallback
    const d = groups.find((g) => g.key === NO_MISSION_CONTEXT_KEY);
    expect(d).toBeDefined();
    expect(d!.jobCount).toBe(3);
    // d has a pending row → live
    expect(d!.isLive).toBe(true);
    expect(d!.missionId).toBeNull();
    expect(d!.instanceId).toBeNull();
  });

  it('preserves server order WITHIN each group (no client-side sort)', () => {
    const jobs = [
      createMockJob({ job_id: 'first', mission_id: 'm-1', instance_id: 'i-1', status: 'pending' }),
      createMockJob({ job_id: 'mid', mission_id: 'm-1', instance_id: 'i-1', status: 'pending' }),
      createMockJob({ job_id: 'last', mission_id: 'm-1', instance_id: 'i-1', status: 'pending' }),
    ];
    const groups = groupJobs(jobs);
    expect(groups.length).toBe(1);
    expect(groups[0].jobs.map((j) => j.job_id)).toEqual(['first', 'mid', 'last']);
  });

  it('uses FIRST-ROW-POSITION for group order (insertion order, deterministic)', () => {
    const jobs = [
      createMockJob({ job_id: '1', mission_id: 'm-A', instance_id: 'i-A' }),
      createMockJob({ job_id: '2', mission_id: 'm-B', instance_id: 'i-B' }),
      createMockJob({ job_id: '3', mission_id: 'm-A', instance_id: 'i-A' }),
      createMockJob({ job_id: '4', mission_id: 'm-C', instance_id: 'i-C' }),
    ];
    const groups = groupJobs(jobs);
    expect(groups.map((g) => g.key)).toEqual(['m-A', 'm-B', 'm-C']);
  });

  it('recycles agentId (first non-null wins across rows)', () => {
    const jobs = [
      createMockJob({
        job_id: '1',
        mission_id: 'm-1',
        instance_id: 'i-1',
        agent_id: null as unknown as string,
        status: 'completed',
      }),
      createMockJob({
        job_id: '2',
        mission_id: 'm-1',
        instance_id: 'i-1',
        agent_id: 'leader',
        status: 'completed',
      }),
    ];
    const groups = groupJobs(jobs);
    expect(groups[0].agentId).toBe('leader');
  });

  it('recycles lastActivityAt as the MOST RECENT created_at across rows', () => {
    const jobs = [
      createMockJob({
        job_id: '1',
        mission_id: 'm-1',
        instance_id: 'i-1',
        created_at: '2026-09-09T10:00:00Z',
      }),
      createMockJob({
        job_id: '2',
        mission_id: 'm-1',
        instance_id: 'i-1',
        created_at: '2026-09-10T10:00:00Z',
      }),
      createMockJob({
        job_id: '3',
        mission_id: 'm-1',
        instance_id: 'i-1',
        created_at: '2026-09-08T10:00:00Z',
      }),
    ];
    const groups = groupJobs(jobs);
    expect(groups[0].lastActivityAt).toBe('2026-09-10T10:00:00Z');
  });

  it('isLive reflects ANY non-terminal row in the group (pending|processing|paused)', () => {
    const groups = groupJobs([
      createMockJob({ job_id: '1', mission_id: 'm-1', instance_id: 'i-1', status: 'settled' }),
      createMockJob({ job_id: '2', mission_id: 'm-1', instance_id: 'i-1', status: 'completed' }),
      createMockJob({ job_id: '3', mission_id: 'm-1', instance_id: 'i-1', status: 'failed' }),
    ]);
    expect(groups[0].isLive).toBe(false);
  });

  it('isLive=true when at least one row is non-terminal', () => {
    const groups = groupJobs([
      createMockJob({ job_id: '1', mission_id: 'm-1', instance_id: 'i-1', status: 'completed' }),
      createMockJob({ job_id: '2', mission_id: 'm-1', instance_id: 'i-1', status: 'pending' }),
      createMockJob({ job_id: '3', mission_id: 'm-1', instance_id: 'i-1', status: 'failed' }),
    ]);
    expect(groups[0].isLive).toBe(true);
  });

  it('handles an empty input without erroring (returns [])', () => {
    expect(groupJobs([])).toEqual([]);
  });

  it('P3 cross-seam invariant: filter change → regrouping keeps Σ jobCount == input length (no orphans)', () => {
    // The plan's cross-seam pin: a filter change that re-groups
    // the dataset (e.g. status filter flips from "all" to a
    // single status) MUST preserve the never-hide invariant on
    // the surviving rows. Σ jobCount across the regrouped
    // output must equal the input length, and every input
    // job_id must appear exactly once in the output.
    const allJobs = [
      // Group A: live
      createMockJob({ job_id: 'a-1', mission_id: 'm-A', instance_id: 'i-A', status: 'pending' }),
      createMockJob({ job_id: 'a-2', mission_id: 'm-A', instance_id: 'i-A', status: 'processing' }),
      // Group B: terminal
      createMockJob({ job_id: 'b-1', mission_id: 'm-B', instance_id: 'i-B', status: 'completed' }),
      createMockJob({ job_id: 'b-2', mission_id: 'm-B', instance_id: 'i-B', status: 'failed' }),
      // Group C: mixed (terminal + live)
      createMockJob({ job_id: 'c-1', mission_id: 'm-C', instance_id: 'i-C', status: 'pending' }),
      createMockJob({ job_id: 'c-2', mission_id: 'm-C', instance_id: 'i-C', status: 'completed' }),
      // Group D: child-bound
      createMockJob({ job_id: 'd-1', mission_id: null, instance_id: 'i-D', status: 'completed' }),
      // Group E: no-context
      createMockJob({ job_id: 'e-1', mission_id: null, instance_id: null, status: 'pending' }),
    ];
    const allLength = allJobs.length; // 8

    // Re-group #1: NO filter — every row in.
    const groupsAll = groupJobs(allJobs);
    const sumAll = groupsAll.reduce((sum, g) => sum + g.jobCount, 0);
    expect(sumAll).toBe(allLength);
    const idsAll = new Set<string>();
    groupsAll.forEach((g) => g.jobs.forEach((j) => idsAll.add(j.job_id)));
    for (const j of allJobs) expect(idsAll.has(j.job_id)).toBe(true);

    // Re-group #2: status filter to "pending" — only a-1, c-1,
    // e-1 survive. The regrouped output MUST have 3 jobs total
    // across the surviving groups (a new group may form when
    // c-1 alone is in m-C, or a group may shrink to a single
    // row). The never-hide invariant holds.
    const pendingJobs = allJobs.filter((j) => j.status === 'pending');
    expect(pendingJobs.length).toBe(3);
    const groupsPending = groupJobs(pendingJobs);
    const sumPending = groupsPending.reduce((sum, g) => sum + g.jobCount, 0);
    expect(sumPending).toBe(pendingJobs.length);
    expect(sumPending).toBe(3);

    // Re-group #3: status filter to "completed" — b-1, c-2,
    // d-1 survive.
    const completedJobs = allJobs.filter((j) => j.status === 'completed');
    expect(completedJobs.length).toBe(3);
    const groupsCompleted = groupJobs(completedJobs);
    const sumCompleted = groupsCompleted.reduce((sum, g) => sum + g.jobCount, 0);
    expect(sumCompleted).toBe(completedJobs.length);
    expect(sumCompleted).toBe(3);

    // Re-group #4: status filter to "failed" — only b-2.
    const failedJobs = allJobs.filter((j) => j.status === 'failed');
    expect(failedJobs.length).toBe(1);
    const groupsFailed = groupJobs(failedJobs);
    const sumFailed = groupsFailed.reduce((sum, g) => sum + g.jobCount, 0);
    expect(sumFailed).toBe(failedJobs.length);
    expect(sumFailed).toBe(1);
    // The single-row group carries its original key.
    expect(groupsFailed[0].key).toBe('m-B');
  });

  it('coalesced-key identity: a row with both fields populated joins the SAME group as a row with only mission_id', () => {
    // The plan's identity rule: when both fields are populated they
    // are equal by the mission identity rule — the fallback cannot
    // re-key a row. A row that ships both and a row that ships only
    // mission_id MUST land in the SAME group.
    const jobs = [
      createMockJob({
        job_id: 'both',
        mission_id: 'm-shared',
        instance_id: 'i-shared',
      }),
      createMockJob({
        job_id: 'mission-only',
        mission_id: 'm-shared',
        instance_id: null,
      }),
    ];
    const groups = groupJobs(jobs);
    expect(groups.length).toBe(1);
    expect(groups[0].jobCount).toBe(2);
    expect(groups[0].key).toBe('m-shared');
  });
});

describe('jobs-grouping — autoExpandGroupIds (G1 port: first-2-live)', () => {
  it('returns the first 2 LIVE group keys only — terminal groups start collapsed', () => {
    const groups = [
      { key: 'g-1', isLive: true } as never,
      { key: 'g-2', isLive: true } as never,
      { key: 'g-3', isLive: false } as never,
      { key: 'g-4', isLive: true } as never,
    ];
    expect(autoExpandGroupIds(groups)).toEqual(['g-1', 'g-2']);
  });

  it('caps at 2 even when more live groups exist (user-locked: exactly 2)', () => {
    const groups = [
      { key: 'g-1', isLive: true } as never,
      { key: 'g-2', isLive: true } as never,
      { key: 'g-3', isLive: true } as never,
      { key: 'g-4', isLive: true } as never,
    ];
    expect(autoExpandGroupIds(groups)).toEqual(['g-1', 'g-2']);
  });

  it('returns at most 2 keys when zero live groups exist', () => {
    const groups = [
      { key: 'g-1', isLive: false } as never,
      { key: 'g-2', isLive: false } as never,
    ];
    expect(autoExpandGroupIds(groups)).toEqual([]);
  });
});

describe('jobs-grouping — compareJobGroups (live-first, activity desc, key asc)', () => {
  it('live groups sort BEFORE terminal groups', () => {
    const live = { key: 'a', isLive: true, lastActivityAt: '2026-09-10T00:00:00Z' } as never;
    const term = { key: 'b', isLive: false, lastActivityAt: '2026-09-11T00:00:00Z' } as never;
    expect(compareJobGroups(live, term)).toBeLessThan(0);
    expect(compareJobGroups(term, live)).toBeGreaterThan(0);
  });

  it('within liveness cluster, activity DESC wins (most recent first)', () => {
    const newer = { key: 'a', isLive: true, lastActivityAt: '2026-09-10T00:00:00Z' } as never;
    const older = { key: 'b', isLive: true, lastActivityAt: '2026-09-09T00:00:00Z' } as never;
    expect(compareJobGroups(newer, older)).toBeLessThan(0);
  });

  it('within same liveness + activity, key ASC tiebreaks (deterministic)', () => {
    const a = { key: 'a', isLive: true, lastActivityAt: '2026-09-10T00:00:00Z' } as never;
    const b = { key: 'b', isLive: true, lastActivityAt: '2026-09-10T00:00:00Z' } as never;
    expect(compareJobGroups(a, b)).toBeLessThan(0);
  });
});

describe('jobs-grouping — defaultGroupTimeAgo (panel port)', () => {
  it('returns "" for null/undefined/empty', () => {
    expect(defaultGroupTimeAgo(null)).toBe('');
    expect(defaultGroupTimeAgo(undefined)).toBe('');
    expect(defaultGroupTimeAgo('')).toBe('');
  });

  it('returns "" for an invalid date string', () => {
    expect(defaultGroupTimeAgo('not-a-date')).toBe('');
  });

  it('uses the same unit ladder as the panel (just now / Xm / Xh / Xd / date)', () => {
    const now = Date.now();
    const sec = (s: number) => new Date(now - s * 1000).toISOString();
    expect(defaultGroupTimeAgo(sec(30))).toBe('just now');
    expect(defaultGroupTimeAgo(sec(120))).toMatch(/^\d+m ago$/);
    expect(defaultGroupTimeAgo(sec(3600))).toMatch(/^\d+h ago$/);
    expect(defaultGroupTimeAgo(sec(86400))).toMatch(/^\d+d ago$/);
  });
});

describe('jobs-grouping — groupMetaLine (agent · N jobs · timeAgo)', () => {
  it('renders agent + N jobs + ago when all three segments are populated', () => {
    expect(
      groupMetaLine({
        agentId: 'leader',
        jobCount: 5,
        lastActivityAt: '2026-09-10T08:00:00Z',
      }),
    ).toContain('leader');
    expect(
      groupMetaLine({
        agentId: 'leader',
        jobCount: 5,
        lastActivityAt: '2026-09-10T08:00:00Z',
      }),
    ).toContain('5 jobs');
    expect(
      groupMetaLine({
        agentId: 'leader',
        jobCount: 5,
        lastActivityAt: '2026-09-10T08:00:00Z',
      }),
    ).toContain('ago');
  });

  it('uses singular "job" when jobCount === 1', () => {
    const line = groupMetaLine({
      agentId: 'leader',
      jobCount: 1,
      lastActivityAt: '2026-09-10T08:00:00Z',
    });
    expect(line).toContain('1 job');
    expect(line).not.toContain('1 jobs');
  });

  it('DROPS the N-jobs segment when jobCount === 0 (panel parity)', () => {
    const line = groupMetaLine({
      agentId: 'leader',
      jobCount: 0,
      lastActivityAt: '2026-09-10T08:00:00Z',
    });
    expect(line).not.toContain('0 jobs');
  });

  it('uses "—" placeholder when agentId is null AND falls back to "idle" when no timestamp', () => {
    // The fallback wording "idle" is M3-prose-safe — NEVER emits "settled".
    const line = groupMetaLine({
      agentId: null,
      jobCount: 3,
      lastActivityAt: null,
    });
    expect(line).toContain('—');
    expect(line).toContain('idle');
    expect(line).not.toContain('settled');
  });

  it('never emits "settled" anywhere (M3 prose rule)', () => {
    const line = groupMetaLine({
      agentId: null,
      jobCount: 0,
      lastActivityAt: null,
    });
    expect(line.toLowerCase()).not.toContain('settled');
  });
});

describe('jobs-grouping — groupHeaderTitle (instanceDisplayTitle port)', () => {
  it('short-circuits the no-context group to NO_MISSION_CONTEXT_TITLE', () => {
    expect(
      groupHeaderTitle({
        key: NO_MISSION_CONTEXT_KEY,
        agentId: null,
        lastActivityAt: null,
      }),
    ).toBe(NO_MISSION_CONTEXT_TITLE);
  });

  it('renders "agent · timeAgo" when both fields are populated', () => {
    const t = groupHeaderTitle({
      key: 'm-1',
      agentId: 'leader',
      lastActivityAt: '2026-09-10T10:00:00Z',
    });
    expect(t).toContain('leader');
    expect(t).toContain('ago');
  });

  it('falls back to agent-only when no timestamp is available', () => {
    expect(
      groupHeaderTitle({
        key: 'm-1',
        agentId: 'leader',
        lastActivityAt: null,
      }),
    ).toBe('leader');
  });

  it('falls back to timeAgo-only when no agent is available', () => {
    const t = groupHeaderTitle({
      key: 'm-1',
      agentId: null,
      lastActivityAt: '2026-09-10T10:00:00Z',
    });
    expect(t).toContain('ago');
    expect(t).not.toContain('·');
  });

  it('returns "" when neither agent nor timestamp is available', () => {
    expect(
      groupHeaderTitle({
        key: 'm-1',
        agentId: null,
        lastActivityAt: null,
      }),
    ).toBe('');
  });

  it('NEVER emits "settled" anywhere (M3 prose rule)', () => {
    const t = groupHeaderTitle({
      key: 'm-1',
      agentId: null,
      lastActivityAt: null,
    });
    expect(t.toLowerCase()).not.toContain('settled');
  });
});