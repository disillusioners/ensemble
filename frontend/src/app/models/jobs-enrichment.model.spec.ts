// jobs-enrichment.model spec — jobs-page-improvement arc, P3 review.
//
// Behavioural pins for the pure title-enrichment target picker
// (extracted out of JobsComponent in the P3 review so the cascade /
// dedup / no-retry / child-bound logic is testable as a plain
// function — the bug class was: a single source-pin passing while
// the production effect cascaded and re-fetched forever). Pins:
//
// * Cap honored (5-eligible fixture ⇒ exactly the first
//   ``MAX_TITLE_ENRICHMENT_FETCHES`` targets).
// * No-context fallback group is NEVER returned.
// * Child-bound groups (``missionId == null``) are NEVER returned.
// * Previously-attempted keys are NEVER returned (cascade
//   prevention — re-pick only when the dataset generation flips).
// * In-flight keys are NEVER returned (concurrent dedup).
// * Previously-failed keys are NEVER returned (no infinite retry).

import {
  MAX_TITLE_ENRICHMENT_FETCHES,
  NO_MISSION_CONTEXT_KEY,
} from './jobs-grouping.model';
import { pickEnrichmentTargets } from './jobs-enrichment.model';
import { createMockJob } from '../testing/job-test-helpers';
import type { JobGroup } from './jobs-grouping.model';

const CAP = MAX_TITLE_ENRICHMENT_FETCHES;

/** Build a minimal JobGroup with the fields the picker reads. */
function makeGroup(
  key: string,
  opts: { missionId: string | null; jobs: number; isLive?: boolean } = {
    missionId: key,
    jobs: 1,
  },
): JobGroup {
  return {
    key,
    missionId: opts.missionId,
    instanceId: opts.missionId,
    agentId: 'developer',
    jobCount: opts.jobs,
    lastActivityAt: '2026-09-10T10:00:00Z',
    isLive: opts.isLive ?? true,
    jobs: Array.from({ length: opts.jobs }, (_, i) =>
      createMockJob({ job_id: `${key}-j-${i}`, mission_id: opts.missionId }),
    ),
  };
}

describe('jobs-enrichment — picker cap (P3 review flagship pin)', () => {
  it('5-eligible-group fixture returns exactly MAX_TITLE_ENRICHMENT_FETCHES (3) targets', () => {
    // The flagship behavioural pin: a 5-group fixture produces
    // EXACTLY 3 targets, in display order. The legacy effect
    // would cascade to 5+ (or duplicate fetches); this is the
    // core fix.
    const groups = [
      makeGroup('g-1', { missionId: 'm-1', jobs: 2 }),
      makeGroup('g-2', { missionId: 'm-2', jobs: 3 }),
      makeGroup('g-3', { missionId: 'm-3', jobs: 1 }),
      makeGroup('g-4', { missionId: 'm-4', jobs: 4 }),
      makeGroup('g-5', { missionId: 'm-5', jobs: 2 }),
    ];
    const attempted = new Set<string>();
    const inFlight = new Set<string>();
    const failed = new Set<string>();
    const overrides = new Map<string, string>();
    const targets = pickEnrichmentTargets(
      groups,
      attempted,
      inFlight,
      failed,
      overrides,
      CAP,
    );
    expect(targets.length).toBe(CAP);
    expect(targets).toEqual(['g-1', 'g-2', 'g-3']);
    // No duplicates.
    expect(new Set(targets).size).toBe(targets.length);
  });

  it('cap is honored even when many more groups are eligible', () => {
    const groups = Array.from({ length: 50 }, (_, i) =>
      makeGroup(`g-${i}`, { missionId: `m-${i}`, jobs: 1 }),
    );
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      new Map(),
      CAP,
    );
    expect(targets.length).toBe(CAP);
  });

  it('CAP = 3 is the plan-pinned value (named constant, not a magic number)', () => {
    expect(MAX_TITLE_ENRICHMENT_FETCHES).toBe(3);
    expect(CAP).toBe(3);
  });
});

describe('jobs-enrichment — no-context fallback never returns', () => {
  it('the explicit no-context group is filtered out', () => {
    const groups = [
      makeGroup('g-1', { missionId: 'm-1', jobs: 1 }),
      // The fallback group — its key is the sentinel string,
      // its missionId is null.
      {
        ...makeGroup(NO_MISSION_CONTEXT_KEY, {
          missionId: null,
          jobs: 5,
        }),
        // missionId null AND instanceId null — the true fallback
        // shape (the picker only checks key, but the model
        // produces this key only when both fields are null).
        instanceId: null,
      },
    ];
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      new Map(),
      CAP,
    );
    expect(targets).toEqual(['g-1']);
    expect(targets).not.toContain(NO_MISSION_CONTEXT_KEY);
  });
});

describe('jobs-enrichment — child-bound (missionId null) groups never return', () => {
  it('child-bound groups (missionId: null, instanceId populated) are filtered out', () => {
    // The coalesced key for a child-bound row is the instance_id.
    // ``GET /api/missions/{instance_id}`` is semantically wrong
    // (the missions endpoint takes a mission_id, not an
    // instance_id). The picker filters on the authoritative
    // missionId field, not the key.
    const groups = [
      makeGroup('i-child-1', { missionId: null, jobs: 2 }),
      makeGroup('m-real-1', { missionId: 'm-real-1', jobs: 3 }),
      makeGroup('i-child-2', { missionId: null, jobs: 4 }),
    ];
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      new Map(),
      CAP,
    );
    // Only the missionId-bearing group is returned. No child-bound
    // group ever appears in the targets, regardless of its
    // coalesced key.
    expect(targets).toEqual(['m-real-1']);
    expect(targets).not.toContain('i-child-1');
    expect(targets).not.toContain('i-child-2');
  });

  it('child-bound fixture with ZERO missionId-bearing groups returns []', () => {
    const groups = [
      makeGroup('i-child-1', { missionId: null, jobs: 1 }),
      makeGroup('i-child-2', { missionId: null, jobs: 2 }),
    ];
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      new Map(),
      CAP,
    );
    expect(targets).toEqual([]);
  });
});

describe('jobs-enrichment — attemptedKeys (cascade prevention)', () => {
  it('a previously-attempted key is never returned in the same picker call', () => {
    const groups = [
      makeGroup('g-1', { missionId: 'm-1', jobs: 1 }),
      makeGroup('g-2', { missionId: 'm-2', jobs: 1 }),
    ];
    const attempted = new Set<string>(['g-1']);
    const targets = pickEnrichmentTargets(
      groups,
      attempted,
      new Set(),
      new Set(),
      new Map(),
      CAP,
    );
    expect(targets).toEqual(['g-2']);
    expect(targets).not.toContain('g-1');
  });

  it('attemptedKeys with all-but-one eligible key returns only the un-attempted one (no cascade)', () => {
    // The cascade-prevention flagship: 5 eligible groups, 4 of
    // them already attempted, picker returns just the 1 left.
    // (Cap check in the picker is moot here — there's only 1
    // candidate after the attempted filter.)
    const groups = Array.from({ length: 5 }, (_, i) =>
      makeGroup(`g-${i}`, { missionId: `m-${i}`, jobs: 1 }),
    );
    const attempted = new Set<string>(['g-0', 'g-1', 'g-2', 'g-3']);
    const targets = pickEnrichmentTargets(
      groups,
      attempted,
      new Set(),
      new Set(),
      new Map(),
      CAP,
    );
    expect(targets).toEqual(['g-4']);
  });
});

describe('jobs-enrichment — inFlightKeys (concurrent dedup)', () => {
  it('a key with an outstanding subscribe is not returned (concurrent dedup)', () => {
    const groups = [
      makeGroup('g-1', { missionId: 'm-1', jobs: 1 }),
      makeGroup('g-2', { missionId: 'm-2', jobs: 1 }),
    ];
    const inFlight = new Set<string>(['g-1']);
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      inFlight,
      new Set(),
      new Map(),
      CAP,
    );
    expect(targets).toEqual(['g-2']);
  });

  it('inFlightKeys filter applies BEFORE attemptedKeys (in-flight wins)', () => {
    // If a key is in BOTH attempted and in-flight, the in-flight
    // check is redundant (attempted alone would skip it). The
    // filter order doesn't matter for the result — but the
    // production subscribe path adds to BOTH sets BEFORE the
    // request fires, so a concurrent picker call sees the key
    // filtered. This test documents the contract.
    const groups = [
      makeGroup('g-1', { missionId: 'm-1', jobs: 1 }),
    ];
    const attempted = new Set<string>(['g-1']);
    const inFlight = new Set<string>(['g-1']);
    const targets = pickEnrichmentTargets(
      groups,
      attempted,
      inFlight,
      new Set(),
      new Map(),
      CAP,
    );
    expect(targets).toEqual([]);
  });
});

describe('jobs-enrichment — failedKeys (no infinite retry)', () => {
  it('a previously-failed key is never returned (no infinite retry across polls)', () => {
    const groups = [
      makeGroup('g-1', { missionId: 'm-1', jobs: 1 }),
      makeGroup('g-2', { missionId: 'm-2', jobs: 1 }),
      makeGroup('g-3', { missionId: 'm-3', jobs: 1 }),
    ];
    const failed = new Set<string>(['g-2']);
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      failed,
      new Map(),
      CAP,
    );
    // g-2 is permanently skipped; g-1 and g-3 are picked.
    expect(targets).toEqual(['g-1', 'g-3']);
    expect(targets).not.toContain('g-2');
  });

  it('failedKeys with all-but-one eligible key returns only the un-failed one', () => {
    const groups = Array.from({ length: 5 }, (_, i) =>
      makeGroup(`g-${i}`, { missionId: `m-${i}`, jobs: 1 }),
    );
    const failed = new Set<string>(['g-0', 'g-1', 'g-2', 'g-3']);
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      failed,
      new Map(),
      CAP,
    );
    expect(targets).toEqual(['g-4']);
  });
});

describe('jobs-enrichment — titleOverrides (already-enriched filter)', () => {
  it('a key with an entry in titleOverrides is skipped (success-side dedup)', () => {
    const groups = [
      makeGroup('g-1', { missionId: 'm-1', jobs: 1 }),
      makeGroup('g-2', { missionId: 'm-2', jobs: 1 }),
    ];
    const overrides = new Map<string, string>([['g-1', 'cached title']]);
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      overrides,
      CAP,
    );
    expect(targets).toEqual(['g-2']);
  });
});

describe('jobs-enrichment — combined fixture (P3 review flagship)', () => {
  it('mixed 5-group fixture: eligible + no-context + child-bound + already-enriched → exactly the right 3', () => {
    // The combined flagship: every filter is exercised in one
    // call. The result must be deterministic and respect every
    // gate.
    const groups = [
      // g-1: mission-bearing, eligible
      makeGroup('g-1', { missionId: 'm-1', jobs: 1 }),
      // no-context fallback: filtered by key
      makeGroup(NO_MISSION_CONTEXT_KEY, { missionId: null, jobs: 5 }),
      // g-2: mission-bearing, already in titleOverrides → skipped
      makeGroup('g-2', { missionId: 'm-2', jobs: 1 }),
      // i-child: child-bound (missionId null) → filtered
      makeGroup('i-child', { missionId: null, jobs: 3 }),
      // g-3: mission-bearing, eligible
      makeGroup('g-3', { missionId: 'm-3', jobs: 1 }),
      // g-4: mission-bearing, eligible
      makeGroup('g-4', { missionId: 'm-4', jobs: 1 }),
      // g-5: mission-bearing, eligible
      makeGroup('g-5', { missionId: 'm-5', jobs: 1 }),
    ];
    const overrides = new Map<string, string>([['g-2', 'cached']]);
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      overrides,
      CAP,
    );
    // Only mission-bearing, not-already-overridden, not in
    // fallback, not child-bound → g-1, g-3, g-4, g-5 eligible.
    // Cap to 3 → first 3 in display order: g-1, g-3, g-4.
    expect(targets).toEqual(['g-1', 'g-3', 'g-4']);
  });
});
