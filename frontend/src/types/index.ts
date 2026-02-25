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

export interface Message {
  type: string;
  message_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  tool_calls?: Array<{
    id: string;
    name: string;
    arguments: string | Record<string, unknown>;
    output?: string;
  }>;
  error?: string;
  created_at: string;
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

export interface HealthResponse {
  status: string;
  uptime_seconds: number;
  version: string;
}

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
