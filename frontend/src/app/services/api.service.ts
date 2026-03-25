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
  AgentCreate,
  Source,
  SourceCreate,
  SourceUpdate,
  SourceListResponse,
  SourceActionResponse,
  SourceTestRequest,
  SourceTestResponse,
  SessionMapping,
  SessionMappingCreate,
  SessionMappingListResponse,
  DeleteResponse
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
  createSession(agentId: string, sessionId?: string): Observable<SessionInfo> {
    return this.http.post<SessionInfo>(`${this.API_BASE}/sessions`, { 
      agent_id: agentId, 
      session_id: sessionId 
    });
  }

  listSessions(limit: number = 100, offset: number = 0): Observable<SessionListResponse> {
    return this.http.get<SessionListResponse>(`${this.API_BASE}/sessions`, {
      params: { limit: limit.toString(), offset: offset.toString() }
    });
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

  // Sources
  listSources(): Observable<SourceListResponse> {
    return this.http.get<SourceListResponse>(`${this.API_BASE}/sources`);
  }

  getSource(sourceId: string): Observable<Source> {
    return this.http.get<Source>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}`);
  }

  createSource(source: SourceCreate): Observable<Source> {
    return this.http.post<Source>(`${this.API_BASE}/sources`, source);
  }

  updateSource(sourceId: string, source: SourceUpdate): Observable<Source> {
    return this.http.put<Source>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}`, source);
  }

  deleteSource(sourceId: string): Observable<DeleteResponse> {
    return this.http.delete<DeleteResponse>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}`);
  }

  startSource(sourceId: string): Observable<SourceActionResponse> {
    return this.http.post<SourceActionResponse>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}/start`, {});
  }

  stopSource(sourceId: string): Observable<SourceActionResponse> {
    return this.http.post<SourceActionResponse>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}/stop`, {});
  }

  testSource(testRequest: SourceTestRequest): Observable<SourceTestResponse> {
    return this.http.post<SourceTestResponse>(`${this.API_BASE}/sources/test`, testRequest);
  }

  // Mappings
  listMappings(sourceId: string): Observable<SessionMappingListResponse> {
    return this.http.get<SessionMappingListResponse>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}/mappings`);
  }

  createMapping(sourceId: string, mapping: SessionMappingCreate): Observable<SessionMapping> {
    return this.http.post<SessionMapping>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}/mappings`, mapping);
  }

  deleteMapping(sourceId: string, mappingId: string): Observable<DeleteResponse> {
    return this.http.delete<DeleteResponse>(`${this.API_BASE}/sources/${sourceId}/mappings/${encodeURIComponent(mappingId)}`);
  }
}
