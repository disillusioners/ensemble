// Instance types
export type InstanceStatus = 'idle' | 'running' | 'waiting' | 'error' | 'terminated';

export interface InstanceInfo {
  instance_id: string;
  agent_id: string;
  status: InstanceStatus;
  parent_id: string | null;
  children: string[];
  title?: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface InstanceListResponse {
  instances: InstanceInfo[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

// Message types
export interface Message {
  type: string;
  message_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  thinking_extracted?: string;
  tool_calls?: ToolCall[];
  error?: string;
  created_at: string;
  // Instance ID for tracking which instance this message belongs to
  // Used for instance validation in SSE event routing
  instance_id?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: string | Record<string, unknown>;
  output?: string;
}

export interface MessageCreate {
  content: string;
}

export interface MessageResponse {
  message_id: string;
  role: string;
  content: string | null;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls: unknown[] | null;
  created_at: string;
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

// SSE Event types
export type EventType = 
  | 'connected' 
  | 'message_queued' 
  | 'message_received'   // New message received (including child reports)
  | 'status_changed' 
  | 'content_chunk' 
  | 'tool_call' 
  | 'thinking'
  | 'tool_complete'
  | 'processing_completed'  // Backend sends this
  | 'message_completed'     // Canonical message state
  | 'completed'             // Legacy/other sources
  | 'cancelled'
  | 'error' 
  | 'keepalive'
  | 'title_updated';

export interface SSEEvent {
  event_id: number;
  type: EventType;
  instance_id: string;
  message_id: string | null;
  data: Record<string, unknown>;
}

// Message delta types for SSE streaming updates
// These are used to update messages in-place in the messages list
export type MessageDeltaType = 
  | 'processing_started'   // New message started processing - add placeholder
  | 'content_chunk'       // Content chunk received - append to message
  | 'thinking'            // Thinking content received - update message
  | 'tool_call'           // Tool call started - add tool to message
  | 'tool_complete'       // Tool call completed - update tool output
  | 'processing_completed' // Message processing done - finalize message
  | 'processing_failed'    // Message processing failed - mark as error
  | 'message_completed'     // Final canonical message - replace accumulated state
  | 'message_received';     // New message received (user input, child reports, etc.)

// Canonical message payload from message_completed event
export interface CanonicalMessage {
  message_id: string;
  instance_id: string;
  role: string;
  content: string;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls?: ToolCall[];
  created_at?: string;
  source?: string | null;
}

export interface MessageDelta {
  type: MessageDeltaType;
  instance_id: string;
  message_id: string;
  // Delta-specific data
  content?: string;           // For content_chunk, thinking
  tool_call?: {              // For tool_call, tool_complete
    id: string;
    name: string;
    arguments?: Record<string, unknown>;
    output?: string;
  };
  success?: boolean;         // For processing_completed
  error?: string;           // For processing_failed
  message?: CanonicalMessage; // For message_completed
  original_message_id?: string; // For message_completed
  // For message_received
  source?: string;
  priority?: number;
  timestamp: string;
}

// Source types
export type SourceStatus = 'stopped' | 'starting' | 'running' | 'error';
export type SourceType = 'telegram' | 'webhook' | 'whatsapp' | 'discord' | 'scheduler';

export interface Source {
  source_id: string;
  source_type: SourceType;
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  status: SourceStatus;
  error_message?: string;
  created_at: string;
  updated_at?: string;
}

export interface SourceCreate {
  source_id: string;
  source_type: SourceType;
  name: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  enabled?: boolean;
}

export interface SourceUpdate {
  name?: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  enabled?: boolean;
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

// Job Queue types
export * from './job-queue.model';
