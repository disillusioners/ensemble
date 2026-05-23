// Inline queue model types to avoid module resolution issues
export type QueueType = 'fifo' | 'parallel' | 'defer';

export interface JobQueue {
  queue_id: string;
  project_id: string;
  queue_name: string;
  queue_type: QueueType;
  concurrency_limit: number;
  is_system: boolean;
  is_paused: boolean;
  description: string | null;
  created_at: string;
  updated_at: string;
  active_jobs: number;
  pending_jobs: number;
}

export function createMockQueue(overrides?: Partial<JobQueue>): JobQueue {
  return {
    queue_id: 'queue-123',
    project_id: 'project-123',
    queue_name: 'Test Queue',
    queue_type: 'fifo',
    concurrency_limit: 5,
    is_system: false,
    is_paused: false,
    description: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    active_jobs: 2,
    pending_jobs: 5,
    ...overrides,
  };
}

export function createMockQueueList(count: number): JobQueue[] {
  return Array.from({ length: count }, (_, i) =>
    createMockQueue({
      queue_id: `queue-${i}`,
      queue_name: `Queue ${i}`,
      active_jobs: i % 3,
      pending_jobs: i * 2,
    })
  );
}
