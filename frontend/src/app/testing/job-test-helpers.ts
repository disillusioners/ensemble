// Re-export the canonical Job/Work models so specs drive the model
// without redefining the types. Kills the stale "inline types to
// avoid module resolution issues" rationale — the model is the
// single source of truth.

import type { Job, JobStatus } from '../models/job.model';

export * from '../models/job.model';
export * from '../models/work.model';

// Backwards-compatible alias for pre-round-2 specs that imported
// the helper module under a local name.
export type HelperJobStatus = JobStatus;

/**
 * Test-only widening of ``JobStatus`` to cover defensive-fallback
 * strings (``'active'`` / ``'queued'``) that some backend paths
 * still emit but are NOT canonical. Encapsulated via
 * ``createMockJobWithStatus`` so call sites stay cast-free.
 */
export type MockJobStatus = JobStatus | 'active' | 'queued';

export function createMockJobWithStatus(status: MockJobStatus, rest?: Partial<Job>): Job {
  return createMockJob({ ...(rest ?? {}), status: status as JobStatus });
}

/**
 * Build a minimal Job with sensible defaults. ``status`` is typed
 * against the re-exported ``JobStatus`` so a missing enum member
 * (e.g. ``'paused'``) is a type error, not a hidden cast.
 */
export function createMockJob(overrides?: Partial<Job>): Job {
  return {
    job_id: 'test-job-123',
    agent_id: 'developer',
    message: 'Fix the login bug',
    source: 'api',
    project_id: 'project-123',
    priority: 5,
    status: 'pending',
    created_at: new Date().toISOString(),
    started_at: null,
    completed_at: null,
    instance_id: null,
    error_message: null,
    result_summary: null,
    job_metadata: {},
    cancelled_at: null,
    deleted_at: null,
    ...overrides,
  };
}

/**
 * Fix C (§8.2) — a terminal mirror receipt whose parent mission is
 * still working. The 28c6421b pair: receipt says "handled",
 * liveness consult says "parent still running".
 */
export function createMockLiveMissionReceipt(overrides?: Partial<Job>): Job {
  return createMockJob({
    job_id: 'mirror-live-1',
    status: 'completed',
    completed_at: new Date().toISOString(),
    instance_id: 'instance-live-leader',
    job_type: 'message',
    mission_liveness: 'processing',
    ...overrides,
  });
}

export function createMockJobList(count: number): Job[] {
  return Array.from({ length: count }, (_, i) =>
    createMockJob({
      job_id: `job-${i}`,
      priority: Math.min(10, i + 1),
      status: i < count / 2 ? 'pending' : 'completed',
    })
  );
}