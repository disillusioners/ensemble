// Work model spec — jobs-page-improvement arc, Phase 1 (P1 row-parity).
//
// Drives the REAL pure ``workToJob`` mapper (moved out of the
// component, where the all-work pipeline bypassed filters and
// hard-nulled the timeline fields). These pins are the behavioral
// anchor for the parity fix; the production-source-text anchor lives
// in jobs-page.bindings.pins.spec.ts (F-5).

import { Job } from './job.model';
import { Work, workToJob } from './work.model';

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
