// Instance types
export type InstanceStatus = 'idle' | 'running' | 'paused' | 'completed' | 'error' | 'terminated' | 'queued' | 'waiting_children' | 'failed';

// ─────────────────────────────────────────────────────────────────────────
// Slash-command subsystem (Phase 2) — architect §7 wire contract, PINNED.
// Encoded VERBATIM from
// .agents/shared/planning/slash-commands/architecture-recommendation.md §7
// (with the post-review adjudication amendment: compacted_type gains
// "partial_summary"). The Jest parseCommandAck adapter test is the
// executable contract spec for these types — any Phase 1 backend drift
// must fail FE CI with a named field (R6).
// ─────────────────────────────────────────────────────────────────────────

/** SSE ``command_progress`` phases — the six-phase machine is unchanged by
 *  the C1 amendment (partial is a detail-level distinction). */
export type CommandPhase =
  | 'waiting'
  | 'in_progress'
  | 'success'
  | 'timed_out'
  | 'fallback_applied'
  | 'failed';

/** Semantic refusals arrive as 200 ack ``state:"rejected"`` + reason.
 *  Unknown COMMANDS are parse-time client errors → HTTP 400
 *  ``UNKNOWN_COMMAND`` instead (§7 split rule). */
export type RejectionReason =
  | 'terminal_instance'
  | 'busy'
  | 'rate_limited'
  | 'pending_injections'
  | 'compaction_disabled'
  | 'quiescence_timeout'
  /** W1 (2026-08-31, contract drift): BE ships an additional rejection
   *  reason — emitted when the daemon has the command registered but the
   *  policy gate (O-B6 per-agent availability, or a future quota check)
   *  refuses to dispatch it. The literal string is pinned in
   *  ``daemon/services/command_dispatcher.py`` and must agree here. */
  | 'unavailable';

/** §7 amendment (post-review adjudication C1, 2026-08-31): the enum gains
 *  "partial_summary". Mapping count = three: summary → success;
 *  partial_summary and truncation → timed_out → fallback_applied;
 *  noop → success (+ noop_reason). */
export type CompactedType = 'summary' | 'partial_summary' | 'truncation' | 'noop';

export type NoopReason = 'below_floor' | 'recently_compacted' | 'too_few_messages';

export type CommandFailureKind = 'timeout' | 'error' | null;

/** Optional detail object on ``CommandProgressEvent``. Budget exhaustion
 *  reports ``failure_kind: "timeout"``; ``detail.reason`` is free-form and
 *  may say ``budget_exhausted``. */
export interface CommandProgressDetail {
  tokens_before?: number;
  tokens_after?: number;
  compacted_type?: CompactedType;
  failure_kind?: CommandFailureKind;
  noop_reason?: NoopReason;
  checkpoint_id?: string;
  reason?: string;
}

/** SSE event_type="command_progress" (LiveEventHub.stream_message,
 *  live-only, no replay). ``phase_seq`` is monotonic per command — the FE
 *  dedup/reorder guard ignores events with ``phase_seq <=`` last seen for
 *  the same ``command_id``. ``elapsed_ms`` (server clock) is the FE
 *  elapsed-timer source of truth. ``eta_ms`` is advisory, in_progress only.
 *  Heartbeat: the backend re-emits in_progress every 10s (phase_seq+1,
 *  fresh timestamp/elapsed_ms). */
export interface CommandProgressEvent {
  instance_id: string;
  command_id: string;
  phase: CommandPhase;
  phase_seq: number;
  timestamp: string;
  elapsed_ms: number;
  eta_ms?: number;
  detail?: CommandProgressDetail;
}

/** POST /api/instances/{id}/messages → command ack (sync, ≤500ms).
 *
 *  Delta note (executable-spec finding, 2026-08-31): §7 pins
 *  ``command_id`` as a UUIDv4 string, but the Phase 1 dispatcher ships
 *  ``command_id: null`` on ``state:"rejected"`` acks (nothing to
 *  correlate). The type therefore carries ``string | null``; the
 *  ``parseCommandAck`` adapter is the single point that absorbs the
 *  difference, and callers never read ``command_id`` on rejections. */
export interface CommandAck {
  status: 'command';
  command: string; // "compact"
  command_id: string | null; // UUIDv4 — correlates ALL events (null on rejected)
  state: 'accepted' | 'rejected';
  reason?: RejectionReason | null; // when rejected
  detail?: string | null; // human guidance (e.g. terminal-instance hint)
  timestamp: string; // ISO8601
  ttl_seconds: number; // GET-fallback memory window (default 600)
}

/** GET /api/instances/{id}/commands/active — fallback for SSE loss; auth
 *  mirrors GET /messages. Daemon restart ⇒ {exists:false} ⇒ FE clears the
 *  card silently. Poll ~5s while the card is active AND SSE is dead. */
export type GetActiveResponse =
  | { exists: false }
  | { exists: true; command: CommandProgressEvent };

/** Extensible command surface (registry seed = /compact). The autocomplete
 *  palette (Task 10) is out of scope this phase; ``availability`` is the
 *  O-B6 per-agent policy hook's landing spot (Q4 — global for now). */
export interface CommandDefinition {
  name: string; // canonical, lowercase, no leading slash
  description: string;
  argsHint?: string | null;
  availability?: () => boolean;
}

export interface InstanceInfo {
  instance_id: string;
  agent_id: string;
  project_id: string | null;
  status: InstanceStatus;
  parent_id: string | null;
  children: string[];
  title?: string | null;
  // Optional display name for the instance. Backend derives it from
  // ``title``/``name``/``instance_metadata.instance_name`` and broadcasts it
  // on the ``instance_created`` SSE event. Older payloads (and older
  // GET /api/instances responses) may omit it, hence the optional+nullable.
  instance_name?: string | null;
  created_at: string;
  updated_at: string | null;
  // UI preferences (pinned + color tag + icon tag). Optional because older
  // backend responses may omit them; the InstancePrefsService writes
  // through PUT /api/instances/{id}/ui-prefs and reads them via
  // GET /api/instances. ``pinned_at`` is set server-side when ``pinned``
  // flips true and used for pinned-first ordering at the same level.
  pinned?: boolean | null;
  color_tag?: string | null;
  icon_tag?: string | null;  // Material Icon name
  pinned_at?: string | null;
  /** Backend Phase 2 S9 — the version tag the instance was created with
   *  (null for base / unversioned agents). Surfaced in the UI as a small
   *  badge next to the instance title when truthy. */
  agent_tag?: string | null;
  // Watchover (Phase 4): security monitoring state. Populated from
  // instance_metadata on the backend. Optional because older responses
  // may omit them.
  watchover_enabled?: boolean;
  watchover_context?: string | null;
  watchover_denial_count?: number;
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
  /**
   * Provisional visual state. Set by the chat component's optimistic append
   * (Phase 2 / message-display-latency) when the POST response carries a
   * ``message_id``. Cleared by:
   *   - the SSE echo upsert (POST-time echo or drain-time re-emit) — the
   *     server has now confirmed the message and a non-pending copy takes
   *     its place in the union-merge refetch;
   *   - the 10-minute wall-clock TTL eviction (message-display-latency §5 item 9);
   *   - a terminal ``status_change`` purge (same).
   * The flag is purely a UI affordance (dim/spinner until consumed) and
   * never participates in dedup — id-keyed dedup is the source of truth.
   */
  pending?: boolean;
  /**
   * Failed-send state (defect #5 fix, 2026-08-31). Set by the chat
   * component's POST error handler when the request to /messages was
   * rejected (HTTP error, timeout, network failure, or any case where
   * the user saw a bubble rendered but the server never persisted the
   * message — e.g. SSE echo arrived before the POST errored, OR the
   * optimistic append path added a bubble for a response shape that
   * ultimately did not survive a subsequent server check). The bubble
   * renders with an error styling + retry affordance. The merge helper
   * does NOT clear this on SSE echo (the server cannot have a message
   * we never sent) and the TTL eviction does NOT touch it (user must
   * retry or explicitly dismiss). The flag is a UI affordance only —
   * id-keyed dedup remains the source of truth.
   */
  failed?: boolean;
  /** Error reason surfaced in the failed-state UI. */
  errorReason?: string;
  /**
   * Queue context the original send was routed to (defect #5 retry
   * fix, 2026-08-31, must-fix #2). Set on the bubble when the chat
   * component's POST error handler marks the send failed — the retry
   * handler reads this back so the retry POST lands on the SAME
   * queue as the original send. A fresh ``activeProjectId``-derived
   * value would silently route the retry to a different queue if the
   * user switched projects between the original fail and the retry
   * click. ``null`` is a meaningful value (user had the queue
   * selector open and selected no queue); ``undefined`` means the
   * stash is absent (older mark paths, BE refetches). Server-blind:
   * never persisted, never emitted over SSE / refetch — but the
   * client-side merge helper preserves it across in-memory SSE
   * echo merges the same way it preserves the ``failed`` flag so
   * a retry that races an echo still finds the stash.
   */
  queue_id?: string | null;
  /**
   * Original-send content the retry must re-POST verbatim
   * (F1 escape-retry fix, 2026-08-31). Set on the bubble when the
   * chat component's POST error handler marks the send failed —
   * the retry handler reads this back so the retry POST carries
   * the SAME string the original send carried. For an ESCAPE-form
   * message (``//x``), the original send POSTed the RAW form
   * (``//x`` — the BE strips one slash and delivers the literal);
   * the rendered bubble carries the delivered (post-strip) form
   * (``/x``) so the user sees what the model saw. Without this
   * stash, ``onRetryFailedMessage`` re-POSTs the bubble's
   * ``content`` (the stripped form), and the BE re-parses it as
   * a REAL slash command — retry performs a DIFFERENT action than
   * the original send. ``null`` is NOT a meaningful value here
   * (retry-content is always a string when stashed); ``undefined``
   * means the stash is absent (older mark paths, BE refetches,
   * non-escape messages where the rendered content happens to
   * match the sent form). Server-blind: never persisted, never
   * emitted over SSE / refetch — the merge helper preserves it
   * across in-memory SSE echo merges the same way it preserves
   * ``queue_id`` so a retry that races an echo still finds the
   * stash.
   */
  retry_content?: string;
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
  /**
   * Server-minted message id. Additive contract per message-display-latency
   * §4.3 item 5: the 200 (IDLE/QUEUED/PAUSED-auto-resume) path has always
   * carried a real id; the 202 (RUNNING / WAITING_CHILDREN injection)
   * branch now also carries one (the same ``echo_id`` re-used by the
   * POST-time ``user_message`` SSE event and the drain-time re-emit).
   *
   * FE MUST treat this as possibly-absent EVERYWHERE:
   *   - Older backends (pre-fix) never include it on 202.
   *   - The PAUSED auto-resume branch can return ``message_id: null``
   *     (see the backend ``send_message`` handler in ``messages.py``).
   *   - New-FE/old-BE degradation: ``onSendMessage`` skips the optimistic
   *     append when absent, falling back to today's render-on-echo flow.
   */
  message_id?: string | null;
  role: string;
  content: string | null;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls: unknown[] | null;
  // message-display-latency fix: ``created_at`` is OPTIONAL on the
  // send-message response. The 202 (RUNNING / WAITING_CHILDREN
  // injection) path now ships it — same value as the POST-time
  // ``user_message`` SSE echo (same-id-same-stamp) — but the legacy
  // 200 (IDLE/QUEUED/PAUSED auto-resume) paths still derive it
  // server-side and may omit it on older backend versions. The
  // component MUST defensively fall back to ``timestamp`` (and
  // ultimately ``new Date().toISOString()``) — see
  // ``chat.component.ts:onSendMessage``. The earlier non-optional
  // type lie hid the BLOCKER (FE getting ``undefined`` and
  // mis-sorting to the top of the list while ``evictPendingByAge``
  // treated NaN as expired).
  created_at?: string | null;
  // message-display-latency fix: the legacy 200 enqueue body always
  // shipped ``timestamp``; the 202 body does too. The component's
  // defensive read of the server-authoritative stamp
  // (``response.created_at ?? response.timestamp ?? ...``) requires
  // this to be in the type. Optional because older backends /
  // transient shapes may omit both ``created_at`` and ``timestamp``.
  timestamp?: string | null;
  // Vision support: base64 data URIs of attached images (up to 3)
  images?: string[];
  // When true, the backend enqueued the message instead of dispatching it
  // straight to the running agent. Allows the UI to render a "queued"
  // indicator until the agent picks the message up.
  queued?: boolean;
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
  /** Backend Phase 2 — version tag for this specific agent entry (null for base). */
  version_tag?: string | null;
  /** Backend Phase 2 — list of all available version tags for this agent id,
   *  including `null` for the base version when one exists. Empty/null for
   *  single-version agents. */
  available_versions?: (string | null)[] | null;
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

// Editor settings types
export type VSCodeStatusState = 'starting' | 'running' | 'stopped' | 'crashed' | 'stopping';

export interface VSCodeStatus {
  status: VSCodeStatusState;
}

export type EditorType = 'builtin' | 'vscode';
