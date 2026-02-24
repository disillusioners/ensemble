import type { SessionInfo, SessionListResponse, MessageResponse, Message, HealthResponse } from '../types';

const API_BASE = import.meta.env.PROD ? '' : '/api';

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Unknown error' }));
    throw new Error(error.detail?.message || error.message || `HTTP ${response.status}`);
  }

  return response.json();
}

export const api = {
  // Health check
  async health(): Promise<HealthResponse> {
    return fetchApi('/health');
  },

  // Sessions
  async createSession(agentDir: string, sessionId?: string): Promise<SessionInfo> {
    return fetchApi('/sessions', {
      method: 'POST',
      body: JSON.stringify({ agent_dir: agentDir, session_id: sessionId }),
    });
  },

  async listSessions(): Promise<SessionListResponse> {
    return fetchApi('/sessions');
  },

  async getSession(sessionId: string): Promise<SessionInfo> {
    return fetchApi(`/sessions/${sessionId}`);
  },

  async deleteSession(sessionId: string): Promise<{ terminated: boolean }> {
    return fetchApi(`/sessions/${sessionId}`, { method: 'DELETE' });
  },

  // Messages
  async sendMessage(sessionId: string, content: string): Promise<MessageResponse> {
    return fetchApi(`/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  },

  async getMessages(sessionId: string): Promise<Message[]> {
    return fetchApi(`/sessions/${sessionId}/messages`);
  },
};
