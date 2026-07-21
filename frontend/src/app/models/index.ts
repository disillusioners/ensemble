// Instance types
export type InstanceStatus = 'idle' | 'running' | 'paused' | 'completed' | 'error' | 'terminated' | 'queued' | 'waiting_children' | 'failed';

export interface InstanceInfo {
  instance_id: string;
  agent_id: string;
  project_id: string | null;
  status: InstanceStatus;
  parent_id: string | null;
  children: string[];
  title?: string | null;
  created_at: string;
  updated_at: string | null;
  // UI preferences (pinned + color tag). Optional because older
  // backend responses may omit them; the InstancePrefsService writes
  // through PUT /api/instances/{id}/ui-prefs and reads them via
  // GET /api/instances. ``pinned_at`` is set server-side when ``pinned``
  // flips true and used for pinned-first ordering at the same level.
  pinned?: boolean | null;
  color_tag?: string | null;
  pinned_at?: string | null;
}

export interface InstanceListResponse {
  instances: InstanceInfo[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

// Message types (aligned with backend UnifiedMessage)
export interface Message {
  message_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls?: ToolCall[];
  source?: string | null;
  created_at: string;
  // Instance ID for tracking which instance this message belongs to
  // Used for instance validation in SSE event routing
  instance_id?: string;
  // Vision support: base64 data URIs of attached images (up to 3)
  images?: string[];
}

// SSE event types
export type MessageEventType =
  | 'user_message'
  | 'assistant_message'
  | 'thinking'
  | 'tool_call'
  | 'tool_result'
  | 'checkpoint'     // Keep for initial load / reconnect
  | 'connected'
  | 'error'
  | 'keepalive';

export interface SSEMessageEvent {
  type: MessageEventType;
  data: {
    instance_id: string;
    message?: Message;        // For individual events
    messages?: Message[];      // For checkpoint events
    checkpoint_id?: string;
  };
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: string | Record<string, unknown>;
  output?: string;
}

export interface MessageCreate {
  content: string;
  // Vision support: base64 data URIs of attached images (up to 3)
  images?: string[];
}

export interface MessageResponse {
  message_id: string;
  role: string;
  content: string | null;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls: unknown[] | null;
  created_at: string;
  // Vision support: base64 data URIs of attached images (up to 3)
  images?: string[];
}

// Health types
export interface HealthResponse {
  status: string;
  uptime_seconds: number;
  version: string;
}

// Agent types
export interface Agent {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  version?: string;
  agent_id: string;
  system?: boolean;
}

export interface AgentListResponse {
  agents: Agent[];
}

export interface AgentCreate {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
}

// Simplified SSE Event types (from checkpoint-based backend)
export type SseEventType =
  | 'connected'
  | 'error'
  | 'keepalive'
  | 'user_message'
  | 'assistant_message'
  | 'thinking'
  | 'tool_call'
  | 'tool_result'
  | 'status_change'
  | 'instance_created';

export interface SSEEvent {
  type: SseEventType;
  data: Record<string, unknown>;
}

// Source types
export type SourceStatus = 'stopped' | 'starting' | 'running' | 'error';
export type SourceType = 'telegram' | 'webhook' | 'whatsapp' | 'discord' | 'slack' | 'scheduler';

export interface Source {
  source_id: string;
  source_type: SourceType;
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  autostart?: boolean;
  status: SourceStatus;
  error_message?: string;
  created_at: string;
  updated_at?: string;
  has_credentials?: boolean;
}

export interface SourceCreate {
  source_id: string;
  source_type: SourceType;
  name: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  enabled?: boolean;
  autostart?: boolean;
}

export interface SourceUpdate {
  name?: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  enabled?: boolean;
  autostart?: boolean;
}

export interface SourceListResponse {
  sources: Source[];
}

export interface SourceActionResponse {
  source_id: string;
  status: SourceStatus;
  message: string;
}

export interface SourceTestRequest {
  source_type: SourceType;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
}

export interface SourceTestResponse {
  success: boolean;
  message: string;
}

// Instance Mapping types
export interface InstanceMapping {
  mapping_id: string;
  source_id: string;
  external_user_id: string;
  agent_instance_id: string;
  agent_id: string;
  metadata?: Record<string, unknown>;
  last_message_at?: string;
  created_at: string;
}

export interface InstanceMappingCreate {
  external_user_id: string;
  agent_id: string;
  metadata?: Record<string, unknown>;
}

export interface InstanceMappingListResponse {
  mappings: InstanceMapping[];
}

export interface DeleteResponse {
  deleted?: boolean;
  terminated?: boolean;
}

// Pause instance response
export interface PauseResponse {
  paused: boolean;
  paused_ids: string[];
  skipped_ids: string[];
}

// Resume instance response
export interface ResumeResponse {
  resumed: boolean;
  resumed_ids: string[];
  skipped_ids: string[];
  message_id?: string;
}

// Job Queue types
export * from './job-queue.model';

// MCP Server types

// Config schema for built-in server templates
export interface ConfigSchemaField {
  key: string;
  label: string;
  type: 'text' | 'number' | 'boolean' | 'select';
  section: 'args' | 'env';
  required?: boolean;
  default?: unknown;
  description?: string;
  min?: number;
  max?: number;
  options?: string[];  // for select type
  arg_format?: 'key_value' | 'flag';
}

export interface McpServer {
  id: string;
  name: string;
  description: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  is_builtin?: boolean;
  config_schema?: ConfigSchemaField[] | null;
  config_schema_version?: string;
  initial_values?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string | null;
}

export interface McpServerCreate {
  name: string;
  description: string | null;
  config?: Record<string, unknown>;
  is_active?: boolean;
}

export interface McpServerUpdate {
  name?: string;
  description?: string | null;
  config?: Record<string, unknown>;
  is_active?: boolean;
}

export interface McpServerListResponse {
  mcp_servers: McpServer[];
}

export interface McpServerDeleteResponse {
  deleted: boolean;
  id: string;
}

export interface McpServerTestConnectionResponse {
  success: boolean;
  message: string;
  tools_count?: number;
}

// Built-in MCP Server Template types
export interface BuiltinServerTemplate {
  name: string;
  display_name: string;
  description: string;
  config_schema: ConfigSchemaField[];
}

export interface BuiltinTemplateListResponse {
  templates: BuiltinServerTemplate[];
}

export interface BuiltinServerConfigure {
  template_name: string;
  values: Record<string, unknown>;
}

// Migration types (SQLite → PostgreSQL)

export type MigrationStatus = 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';
export type MigrationDatabase = 'sqlite' | 'postgres';
// 'unknown' is the fallback when the daemon is unreachable so the UI
// doesn't lie about which DB is active (vs. a misleading hard-coded default).
export type MigrationDatabaseState = MigrationDatabase | 'unknown';

export interface MigrationAvailability {
  /**
   * True when a SQLite→PostgreSQL migration can be started right now
   * (i.e. running on SQLite AND PostgreSQL env is configured). Stays
   * false once the migration has already completed in-process — the
   * operator must restart the daemon before re-running.
   */
  migration_available: boolean;
  current_database: MigrationDatabaseState;
  postgres_configured: boolean;
  can_start: boolean;
  /**
   * True when PostgreSQL env vars (POSTGRES_HOST + POSTGRES_DB, or a
   * full DSN via DATABASE_URL_POSTGRES) were ever set on the running
   * daemon. Sticky: persists across migrations so the Database menu
   * stays visible even after the active database flips to PostgreSQL.
   */
  postgres_env_set: boolean;
  /**
   * True when the operator may flip the active database via
   * ``POST /api/database/switch`` (e.g. PG is the active database and
   * the SQLite source is still present, or vice-versa).
   */
  can_switch: boolean;
}

export interface MigrationDatabaseSwitchResponse {
  message: string;
  requires_restart: boolean;
}

export interface MigrationProgress {
  status: MigrationStatus;
  current_phase: string | null;
  current_table: string | null;
  tables_completed: number;
  tables_total: number;
  checkpoints_migrated: number;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  requires_restart: boolean;
}

export interface MigrationStartResponse {
  migration_id: string;
  status: MigrationStatus;
  message: string;
}

export interface MigrationCancelResponse {
  status: MigrationStatus;
  message: string;
}

export type MigrationLogLevel = 'info' | 'warning' | 'error' | 'debug';

export interface MigrationLogEntry {
  level: MigrationLogLevel;
  message: string;
  timestamp: string;
}
