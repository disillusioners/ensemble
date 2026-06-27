// Job Queue Models for Frontend

export type JobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled' | 'dead_letter';

export type JobSource = 'api' | 'telegram' | 'scheduler' | 'webhook';

/**
 * WorkKind subset that may appear on a Job record.
 *
 * Only ``'job'`` is meaningful for jobs surfaced through
 * ``JobService``; ``'turn'`` / ``'report'`` are reserved for the
 * unified Work surface so a Job-shaped object synthesised from a
 * ``Work`` record can carry the kind forward without re-typing the
 * ``work.model`` namespace everywhere.
 */
export type JobWorkKind = 'job' | 'turn' | 'report';

export interface Job {
  job_id: string;
  agent_id: string;
  message?: string;
  source?: JobSource;
  project_id: string | null;
  priority: number; // 1-10
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  instance_id: string | null;
  error_message: string | null;
  result_summary: string | null;
  job_metadata?: Record<string, any> | null;
  queue_id?: string | null; // queue this job belongs to
  cancelled_at: string | null;
  deleted_at?: string | null;
  position?: number; // queue position if pending
  // Dead Letter Queue fields
  dlq_reason?: string | null; // reason for moving to DLQ
  retry_count?: number; // number of retries before going to DLQ
  moved_to_dlq_at?: string | null; // timestamp when moved to DLQ
  // Virtual Job Management Surface (Phase 4): work kind discriminator.
  // Optional for backward compatibility — existing JobService responses
  // omit it, in which case the card treats the record as a real
  // queued job (kind === 'job') and shows the queue badge as before.
  kind?: JobWorkKind;
}

export interface JobCreate {
  agent_id: string;
  message: string;
  project_id?: string;
  priority?: number;
  source?: JobSource;
  queue_id?: string;
  metadata?: Record<string, any>;
  image_urls?: string[]; // base64 data URIs for vision support
}

export interface JobFilters {
  status?: JobStatus[];
  source?: JobSource;
  agent_id?: string;
  project_id?: string;
  queue_id?: string;
  include_deleted?: boolean;
}

export interface JobEventPayload {
  job_id: string;
  status?: JobStatus;
  previous_status?: JobStatus;
  instance_id?: string;
  result_summary?: string;
  error_message?: string;
  queue_id?: string | null;
  image_urls?: string[]; // base64 data URIs for vision support
}

export interface JobEvent {
  event: 'connected' | 'status_update' | 'completed' | 'error' | 'keepalive';
  data: JobEventPayload | null;
  image_urls?: string[]; // base64 data URIs for vision support
}

// Helper Functions

export function isTerminalStatus(status: JobStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled' || status === 'dead_letter';
}

export function isJobDeleted(job: Job): boolean {
  return !!job.deleted_at;
}

export function getStatusColor(status: JobStatus): string {
  switch (status) {
    case 'pending':
      return '#9CA3AF'; // gray-400
    case 'processing':
      return '#3B82F6'; // blue-500
    case 'completed':
      return '#22C55E'; // green-500
    case 'failed':
      return '#EF4444'; // red-500
    case 'cancelled':
      return '#F59E0B'; // amber-500
    case 'dead_letter':
      return '#7C3AED'; // purple-600
    default:
      return '#9CA3AF'; // gray-400
  }
}

export function getPriorityColor(priority: number): string {
  if (priority >= 8) return '#EF4444'; // red-500 - high priority
  if (priority >= 5) return '#F59E0B'; // amber-500 - medium-high
  if (priority >= 3) return '#3B82F6'; // blue-500 - medium
  return '#22C55E'; // green-500 - low priority
}

// Dead Letter Queue Models

export interface DeadLetterItem {
  dlq_id: string;
  job_id: string;
  agent_id: string;
  agent_dir: string;
  message: string;
  source: string;
  project_id: string;
  queue_id: string | null;
  error_message: string | null;
  retry_count: number;
  failed_at: string | null;
  moved_to_dlq_at: string;
  reason: string;
  metadata?: Record<string, any> | null;
}

export interface RetryAllResult {
  replayed: number;
  failed: number;
  errors: { dlq_id: string; error: string }[];
}

// DLQ Replay Response (from /api/projects/{projectId}/dlq/{dlqId}/replay)
export interface DLQReplayResponse {
  job_id: string;
  status: string;
  message: string;
}

// DLQ List Response wrapper
export interface DLQListResponse {
  items: DeadLetterItem[];
  total: number;
}
