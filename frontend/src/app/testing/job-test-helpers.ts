// Inline job model types to avoid module resolution issues
export type JobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled' | 'dead_letter';
export type JobSource = 'api' | 'telegram' | 'scheduler' | 'webhook';

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

export function createMockJobList(count: number): Job[] {
  return Array.from({ length: count }, (_, i) =>
    createMockJob({
      job_id: `job-${i}`,
      priority: Math.min(10, i + 1),
      status: i < count / 2 ? 'pending' : 'completed',
    })
  );
}
