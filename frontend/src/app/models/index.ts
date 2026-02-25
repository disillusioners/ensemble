// Session types
export type SessionStatus = 'idle' | 'running' | 'waiting' | 'error' | 'terminated';

export interface SessionInfo {
  session_id: string;
  agent_dir: string;
  status: SessionStatus;
  parent_id: string | null;
  children: string[];
  created_at: string;
  updated_at: string | null;
}

export interface SessionListResponse {
  sessions: SessionInfo[];
}

// Message types
export interface Message {
  type: string;
  message_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
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
  agent_dir: string;
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
  | 'completed' 
  | 'error' 
  | 'keepalive';

export interface SSEEvent {
  event_id: number;
  type: EventType;
  session_id: string;
  message_id: string | null;
  data: Record<string, unknown>;
}
