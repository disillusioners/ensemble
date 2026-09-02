// Job Queue Models for Frontend

export type JobStatus = 'pending' | 'processing' | 'paused' | 'completed' | 'failed' | 'cancelled' | 'dead_letter';

export type JobSource = 'api' | 'telegram' | 'scheduler' | 'webhook';

/**
 * JobItem-side kind discriminator (Fix C read-model split,
 * docs/job-task-system.md §8.2).
 *
 * * ``'task'``    — mission row: the JobItem IS the mission; its
 *   ``status`` is the lifecycle answer (one answer, no split).
 * * ``'message'`` — mirror row: a receipt proving the message was
 *   handled; its ``status`` is the receipt answer and
 *   ``mission_liveness`` carries the parent-mission answer.
 *
 * ``undefined``/``null`` means the wire did not carry the field
 * (Task-backed records — e.g. report rows synthesised from
 * ``/api/work`` — have no JobItem, hence no job_type).
 */
export type JobJobType = 'task' | 'message';

/**
 * Canonical liveness of the linked instance behind a mirror row.
 *
 * Value space is exactly the canonical projection of InstanceStatus
 * (``canonicalize_status``): pending, processing, paused,
 * completed, failed, cancelled. Use values verbatim — the FE never
 * invents a state for ``null`` (mission row / degraded lookup /
 * no linked instance are indistinguishable by design; all ``null``s
 * render nothing extra and fall back to receipt-only semantics).
 */
export type MissionLiveness = 'pending' | 'processing' | 'paused' | 'completed' | 'failed' | 'cancelled';

/**
 * WorkKind subset that may appear on a Job record.
 *
 * Only ``'job'`` is meaningful for jobs surfaced through
 * ``JobService``; ``'report'`` is reserved for the unified Work
 * surface so a Job-shaped object synthesised from a ``Work`` record
 * can carry the kind forward without re-typing the ``work.model``
 * namespace everywhere. ``'turn'`` was removed in Phase 4 partial
 * collapse (2026-07-06) — message turns are now JobItems.
 */
export type JobWorkKind = 'job' | 'report';

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
  // Fix C read-model split (§8.2): JobItem-side discriminator.
  // 'task' = mission row, 'message' = mirror/receipt row, null/
  // undefined = Task-backed record (no JobItem) or legacy payload.
  job_type?: JobJobType | null;
  // Fix C read-model split (§8.2): canonical status of the linked
  // instance, populated ONLY for mirror rows. null means mission
  // row / degraded lookup / no linked instance — by design; render
  // nothing extra rather than inventing a state.
  mission_liveness?: MissionLiveness | null;
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
  // Fix C split-semantics SSE payloads (_ResolvedWork) also carry the
  // discriminator + liveness pair. Optional — legacy payloads omit them.
  job_type?: JobJobType | null;
  mission_liveness?: MissionLiveness | null;
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

/**
 * Paused jobs are non-terminal but suspended (instance paused). The
 * Jobs UI treats ``paused`` as an active state — a paused job can be
 * resumed (via its instance) or cancelled, so it must stay visible in
 * the default list and be selectable as a status filter.
 */
export function isPausedStatus(status: JobStatus): boolean {
  return status === 'paused';
}

/**
 * Whether a job is in an active (non-terminal) state the operator may
 * want to monitor: pending, processing, or paused. Used to decide
 * whether a status filter selection should keep the row visible.
 */
export function isActiveStatus(status: JobStatus): boolean {
  return status === 'pending' || status === 'processing' || status === 'paused';
}

export function isJobDeleted(job: Job): boolean {
  return !!job.deleted_at;
}

// ── Fix C read-model split helpers (§8.2) ────────────────────────────────

/**
 * True when the row is a mirror/receipt row (JobItem kind
 * ``'message'``). Only mirror rows carry the split semantics —
 * receipt chip + mission-liveness indicator. Mission rows
 * (``'task'``) and Task-backed records (no job_type) render
 * nothing extra: a mission row's own ``status`` IS the liveness
 * signal.
 */
export function isReceiptRow(job: Pick<Job, 'job_type'>): boolean {
  return job.job_type === 'message';
}

/**
 * Style split for ``mission_liveness`` values, used verbatim from
 * the wire. Live = the parent mission is still working (pending /
 * processing / paused — the non-terminal cluster). Settled = the
 * parent mission reached a terminal canonical state (completed /
 * failed / cancelled).
 *
 * ``dead_letter`` is deliberately absent: it exists in the job-row
 * admission domain but is unreachable from the instance-status
 * domain ``mission_liveness`` reads (see §8.2 value space).
 */
export function isLiveMissionLiveness(value: MissionLiveness): boolean {
  return value === 'pending' || value === 'processing' || value === 'paused';
}

export function isSettledMissionLiveness(value: MissionLiveness): boolean {
  return !isLiveMissionLiveness(value);
}

/**
 * Chip colour for a ``mission_liveness`` value. Mirrors the Job
 * status palette for the overlapping names so a live mission reads
 * like an active job and a settled mission reads like its terminal
 * status. ``pending`` has no InstanceStatus source today (forward-
 * compat member of the ratified value space) and maps to gray.
 */
export function getMissionLivenessColor(value: MissionLiveness): string {
  switch (value) {
    case 'pending':
      return '#9CA3AF'; // gray-400
    case 'processing':
      return '#3B82F6'; // blue-500
    case 'paused':
      return '#F59E0B'; // amber-500
    case 'completed':
      return '#22C55E'; // green-500
    case 'failed':
      return '#EF4444'; // red-500
    case 'cancelled':
      return '#F59E0B'; // amber-500
    default:
      return '#9CA3AF'; // gray-400
  }
}

/**
 * Render decision for the mission-liveness indicator on a row.
 *
 * Returns ``null`` — render NOTHING extra — for every case the
 * contract keeps silent: mission rows (``job_type='task'``),
 * Task-backed records (no ``job_type``), degraded lookups, and
 * rows with no linked instance. All of those arrive as
 * ``mission_liveness`` absent or ``null`` and are
 * indistinguishable by design (§8.2); the FE never invents a state
 * for them.
 *
 * For mirror rows with a non-null ``mission_liveness`` it returns
 * the verbatim value plus the derived live/settled styling split.
 */
export interface MissionLivenessChip {
  value: MissionLiveness;
  live: boolean;
  label: string; // "mission: processing" — canonical value verbatim
  color: string;
}

export function missionLivenessChip(
  job: Pick<Job, 'job_type' | 'mission_liveness'>
): MissionLivenessChip | null {
  if (!isReceiptRow(job)) return null;
  const value = job.mission_liveness;
  if (!value) return null;
  return {
    value,
    live: isLiveMissionLiveness(value),
    label: `mission: ${value}`,
    color: getMissionLivenessColor(value),
  };
}

export function getStatusColor(status: JobStatus): string {
  switch (status) {
    case 'pending':
      return '#9CA3AF'; // gray-400
    case 'processing':
      return '#3B82F6'; // blue-500
    case 'paused':
      return '#F59E0B'; // amber-500 — suspended, non-terminal
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
