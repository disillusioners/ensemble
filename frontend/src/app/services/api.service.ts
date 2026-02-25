import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import type { 
  SessionInfo, 
  SessionListResponse, 
  MessageResponse, 
  Message, 
  HealthResponse, 
  AgentListResponse, 
  Agent,
  AgentCreate
} from '../models';

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private readonly API_BASE = '/api';

  constructor(private http: HttpClient) {}

  // Health check
  health(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>(`${this.API_BASE}/health`);
  }

  // Agents
  listAgents(): Observable<AgentListResponse> {
    return this.http.get<AgentListResponse>(`${this.API_BASE}/agents`);
  }

  createAgent(agent: AgentCreate): Observable<Agent> {
    return this.http.post<Agent>(`${this.API_BASE}/agents`, agent);
  }

  deleteAgent(agentId: string): Observable<{ deleted: boolean; agent_id: string }> {
    return this.http.delete<{ deleted: boolean; agent_id: string }>(`${this.API_BASE}/agents/${agentId}`);
  }

  // Sessions
  createSession(agentDir: string, sessionId?: string): Observable<SessionInfo> {
    return this.http.post<SessionInfo>(`${this.API_BASE}/sessions`, { 
      agent_dir: agentDir, 
      session_id: sessionId 
    });
  }

  listSessions(): Observable<SessionListResponse> {
    return this.http.get<SessionListResponse>(`${this.API_BASE}/sessions`);
  }

  getSession(sessionId: string): Observable<SessionInfo> {
    return this.http.get<SessionInfo>(`${this.API_BASE}/sessions/${sessionId}`);
  }

  deleteSession(sessionId: string): Observable<{ terminated: boolean }> {
    return this.http.delete<{ terminated: boolean }>(`${this.API_BASE}/sessions/${sessionId}`);
  }

  // Messages
  sendMessage(sessionId: string, content: string): Observable<MessageResponse> {
    return this.http.post<MessageResponse>(`${this.API_BASE}/sessions/${sessionId}/messages`, { content });
  }

  getMessages(sessionId: string): Observable<Message[]> {
    return this.http.get<Message[]>(`${this.API_BASE}/sessions/${sessionId}/messages`);
  }
}
