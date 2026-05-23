// Job Queue Models for Frontend

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

export interface JobQueueCreateRequest {
  queue_name: string;
  queue_type: QueueType;
  concurrency_limit?: number;
  description?: string;
}

export interface JobQueueUpdateRequest {
  queue_name?: string;
  queue_type?: QueueType;
  concurrency_limit?: number;
  description?: string;
}

export interface JobQueueListResponse {
  queues: JobQueue[];
  total: number;
}

// Helper Functions

export function getQueueStatusColor(paused: boolean): string {
  return paused ? '#F59E0B' : '#22C55E'; // amber if paused, green if running
}

export function getQueueStatusLabel(paused: boolean): string {
  return paused ? 'Paused' : 'Running';
}

export function getQueueTypeIcon(type: QueueType): string {
  switch (type) {
    case 'fifo':
      return 'view_list';
    case 'parallel':
      return 'account_tree';
    case 'defer':
      return 'schedule';
  }
}

export function getQueueTypeLabel(type: QueueType): string {
  switch (type) {
    case 'fifo':
      return 'FIFO';
    case 'parallel':
      return 'Parallel';
    case 'defer':
      return 'Defer';
  }
}
