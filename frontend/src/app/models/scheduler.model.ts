// Scheduler Models for Frontend

// Schedule status type
export type ScheduleStatus = 'running' | 'stopped';

// Schedule type (how the schedule is configured)
export type ScheduleType = 'cron' | 'interval' | 'one-time';

// Schedule execution status type
export type ExecutionStatus = 'pending' | 'running' | 'completed' | 'failed';

// Session mode type
export type SessionMode = 'new_session' | 'reuse_session';

// Schedule configuration - what the schedule will run
export interface ScheduleConfiguration {
  type: ScheduleType;            // cron | interval | one-time
  expression?: string;             // Cron expression (e.g., "0 9 * * *") for cron type
  interval_seconds?: number;       // Interval in seconds for interval type
  run_at?: string;                // ISO datetime for one-time execution
  agent: string;                   // Agent name/ID to handle the message
  message: string;                // The message to send to the agent
  timezone?: string;               // Default 'UTC'
  project?: string;               // Optional project context
  session_mode?: SessionMode;     // new_session | reuse_session (default: new_session)
  metadata?: Record<string, any>; // Optional metadata
}

// Main Schedule entity
export interface Schedule {
  id: string;
  name: string;
  config: ScheduleConfiguration;
  status: ScheduleStatus;
  created_at: string;
  updated_at: string;
  last_run_at?: string;
  next_run_at?: string;
}

// Schedule execution record
export interface ScheduleExecution {
  id: string;
  schedule_id: string;
  status: ExecutionStatus;
  started_at: string;
  completed_at?: string;
  instance_id?: string;   // Resulting instance from execution
  error?: string;        // Error message if failed
}

// Request types for creating/updating schedules
export interface ScheduleCreateRequest {
  name: string;
  config: ScheduleConfiguration;
}

export interface ScheduleUpdateRequest {
  name?: string;
  config?: Partial<ScheduleConfiguration>;
}

// List response types
export interface ScheduleListResponse {
  schedules: Schedule[];
}

export interface ExecutionListResponse {
  executions: ScheduleExecution[];
}

// Helper Functions

export function isScheduleActive(schedule: Schedule): boolean {
  return schedule.status === 'running';
}

export function getScheduleStatusColor(status: ScheduleStatus): string {
  switch (status) {
    case 'running':
      return '#22C55E'; // green-500
    case 'stopped':
      return '#9CA3AF'; // gray-400
    default:
      return '#9CA3AF'; // gray-400
  }
}

export function getExecutionStatusColor(status: ExecutionStatus): string {
  switch (status) {
    case 'pending':
      return '#9CA3AF'; // gray-400
    case 'running':
      return '#3B82F6'; // blue-500
    case 'completed':
      return '#22C55E'; // green-500
    case 'failed':
      return '#EF4444'; // red-500
    default:
      return '#9CA3AF'; // gray-400
  }
}

export function isExecutionTerminal(status: ExecutionStatus): boolean {
  return status === 'completed' || status === 'failed';
}
