// Session types
export type SessionStatus = 'idle' | 'running' | 'waiting' | 'error' | 'terminated';

export interface SessionInfo {
  session_id: string;
  agent_id: string;
  status: SessionStatus;
  parent_id: string | null;
  children: string[];
  title?: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface SessionListResponse {
  sessions: SessionInfo[];
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
  | 'status_changed' 
  | 'content_chunk' 
  | 'tool_call' 
  | 'thinking'
  | 'tool_complete'
  | 'completed' 
  | 'error' 
  | 'keepalive'
  | 'title_updated';

export interface SSEEvent {
  event_id: number;
  type: EventType;
  session_id: string;
  message_id: string | null;
  data: Record<string, unknown>;
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

// Session Mapping types
export interface SessionMapping {
  mapping_id: string;
  source_id: string;
  external_user_id: string;
  agent_session_id: string;
  agent_id: string;
  metadata?: Record<string, unknown>;
  last_message_at?: string;
  created_at: string;
}

export interface SessionMappingCreate {
  external_user_id: string;
  agent_id: string;
  metadata?: Record<string, unknown>;
}

export interface SessionMappingListResponse {
  mappings: SessionMapping[];
}

export interface DeleteResponse {
  deleted?: boolean;
  terminated?: boolean;
}
