// Work model spec — jobs-page-improvement arc, Phase 1 (P1 row-parity).
//
// Drives the REAL pure ``workToJob`` mapper (moved out of the
// component, where the all-work pipeline bypassed filters and
// hard-nulled the timeline fields). These pins are the behavioral
// anchor for the parity fix; the production-source-text anchor lives
// in jobs-page.bindings.pins.spec.ts (F-5).

import { Job } from './job.model';
import { Work, workToJob } from './work.model';
import { groupJobs } from './jobs-grouping.model';
import { MAX_TITLE_ENRICHMENT_FETCHES, pickEnrichmentTargets } from './jobs-enrichment.model';

function makeWork(overrides: Partial<Work>): Work {
  return {
    work_id: 'work-1',
    kind: 'job',
    status: 'processing',
    instance_id: null,
    project_id: 'project-123',
    agent_id: 'developer',
    result_summary: null,
    error: null,
    created_at: '2026-09-10T00:00:00Z',
    started_at: null,
    completed_at: null,
    // Real /api/work wire shape: WorkRecord.to_dict() ships BOTH
    // mission keys on EVERY row (mission_projection_to_dict is
    // unconditional; daemon/services/work_resolver.py). Base fixture
    // carries them as null (degraded/child-row values); tests that
    // assert carry-through override with populated values.
    mission_id: null,
    mission_ref: null,
    ...overrides,
  };
}

describe('workToJob — P1 row parity (all-work Timeline fix)', () => {
  it('carries started_at / completed_at from the wire (pre-P1 hard-nulled both)', () => {
    const work = makeWork({
      started_at: '2026-09-10T01:02:03Z',
      completed_at: '2026-09-10T04:05:06Z',
    });
    const job = workToJob(work);
    expect(job.started_at).toBe('2026-09-10T01:02:03Z');
    expect(job.completed_at).toBe('2026-09-10T04:05:06Z');
  });

  it('maps absent wire timings to null (honest "never started/finished", never fabricated)', () => {
    const job = workToJob(makeWork({ started_at: undefined, completed_at: undefined }));
    expect(job.started_at).toBeNull();
    expect(job.completed_at).toBeNull();
  });

  it('carries result_summary and error through (card messagePreview + error surface)', () => {
    const job = workToJob(makeWork({
      result_summary: 'did the thing',
      error: 'half-broken',
    }));
    expect(job.result_summary).toBe('did the thing');
    expect(job.error_message).toBe('half-broken');
  });

  it('message stays undefined (honest gap-e6: no BE surface carries report message content)', () => {
    const job = workToJob(makeWork({}));
    expect(job.message).toBeUndefined();
  });

  it('identity + discriminator fields survive (Fix C §8.2 pairing)', () => {
    const job = workToJob(makeWork({
      work_id: 'w-9',
      kind: 'report',
      job_type: 'task',
      mission_liveness: 'processing',
      instance_id: 'inst-1',
      agent_id: null,
    }));
    expect(job.job_id).toBe('w-9');
    expect(job.kind).toBe('report');
    expect(job.job_type).toBe('task');
    expect(job.mission_liveness).toBe('processing');
    expect(job.instance_id).toBe('inst-1');
    expect(job.agent_id).toBe(''); // null agent → empty string (matches nothing in the agent filter)
  });

  it('queue_id is pinned null for every kind (queue badge guardrail holds on all-work rows)', () => {
    expect(workToJob(makeWork({ kind: 'job' })).queue_id).toBeNull();
    expect(workToJob(makeWork({ kind: 'report' })).queue_id).toBeNull();
  });

  it('status canonical pass-through with defensive fallback', () => {
    expect(workToJob(makeWork({ status: 'processing' })).status).toBe('processing');
    expect(workToJob(makeWork({ status: '' })).status).toBe('pending');
  });

  it('source stays undefined (gap-e5: /api/work carries no source concept — queues-only filter)', () => {
    expect(workToJob(makeWork({})).source).toBeUndefined();
  });
});

describe('workToJob — mission carry-through (all-work title fix)', () => {
  // REAL /api/work row shape: WorkRecord.to_dict() always emits
  // mission_id + mission_ref (unconditional splat of
  // mission_projection_to_dict + the M2 keys in
  // daemon/services/work_resolver.py to_dict). mission_id ==
  // instance_id per mission-class spec §3; mission_ref is the M2
  // cross-reference {mission_id, agent_id, liveness}.
  const REAL_WIRE_MISSION_ID = 'inst-1';
  const REAL_WIRE_MISSION_REF = {
    mission_id: 'inst-1',
    agent_id: 'developer',
    liveness: 'processing',
  };

  it('carries mission_id + mission_ref from a REAL-wire row through the mapping (pre-fix dropped both)', () => {
    const job = workToJob(makeWork({
      instance_id: REAL_WIRE_MISSION_ID,
      mission_id: REAL_WIRE_MISSION_ID,
      mission_ref: REAL_WIRE_MISSION_REF,
    }));
    expect(job.mission_id).toBe('inst-1');
    expect(job.mission_ref).toEqual(REAL_WIRE_MISSION_REF);
  });

  it('maps absent wire mission fields to null (child-bound semantics, never a fabricated identity)', () => {
    const job = workToJob(makeWork({ mission_id: undefined, mission_ref: undefined }));
    expect(job.mission_id).toBeNull();
    expect(job.mission_ref).toBeNull();
  });

  it('all-work group with a carried mission_id becomes ENRICHMENT-ELIGIBLE (cross-seam: map → group → picker)', () => {
    // The full all-work pipeline seam: workToJob → groupJobs →
    // pickEnrichmentTargets. Pre-fix, job.mission_id was undefined →
    // group.missionId null → picker skipped the group forever (this
    // test fails on the pre-fix mapper).
    const job = workToJob(makeWork({
      instance_id: REAL_WIRE_MISSION_ID,
      mission_id: REAL_WIRE_MISSION_ID,
      mission_ref: REAL_WIRE_MISSION_REF,
    }));
    const groups = groupJobs([job]);
    expect(groups).toHaveLength(1);
    expect(groups[0].missionId).toBe('inst-1');
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      new Map(),
      MAX_TITLE_ENRICHMENT_FETCHES,
    );
    expect(targets).toContain('inst-1');
  });

  it('wire row WITHOUT mission_id still groups child-bound (missionId null → picker never picks it)', () => {
    // Child-bound/degraded row: the wire omits mission_id (or ships
    // it null) → null on the Job. The group coalesces onto
    // instance_id, missionId stays null, and the picker's
    // ``!g.missionId`` gate must keep skipping it (fetching
    // GET /api/missions/{instance_id} is semantically wrong).
    const job = workToJob(makeWork({ instance_id: 'child-i', mission_id: null, mission_ref: null }));
    const groups = groupJobs([job]);
    expect(groups[0].missionId).toBeNull();
    expect(groups[0].key).toBe('child-i');
    const targets = pickEnrichmentTargets(
      groups,
      new Set(),
      new Set(),
      new Set(),
      new Map(),
      MAX_TITLE_ENRICHMENT_FETCHES,
    );
    expect(targets).toEqual([]);
  });
});
