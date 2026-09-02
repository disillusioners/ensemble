// Inline job model types to avoid module resolution issues
export type JobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled' | 'dead_letter';
export type JobSource = 'api' | 'telegram' | 'scheduler' | 'webhook';
// Fix C read-model split (§8.2)
export type JobJobType = 'task' | 'message';
export type MissionLiveness = 'pending' | 'processing' | 'paused' | 'completed' | 'failed' | 'cancelled';

export interface Job {
  job_id: string;
  agent_id: string;
  message?: string;
  source?: JobSource;
  project_id: string | null;
  priority: number;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  instance_id: string | null;
  error_message: string | null;
  result_summary: string | null;
  job_metadata?: Record<string, any> | null;
  cancelled_at: string | null;
  deleted_at?: string | null;
  position?: number;
  job_type?: JobJobType | null;
  mission_liveness?: MissionLiveness | null;
}

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
 * still working. This is the 28c6421b pair: the receipt says
 * "handled", the liveness consult says "parent still running".
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
