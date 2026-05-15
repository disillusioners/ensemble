import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import type { 
  InstanceInfo, 
  InstanceListResponse, 
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
  InstanceMapping,
  InstanceMappingCreate,
  InstanceMappingListResponse,
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

  // Instances
  createInstance(agentId: string, instanceId?: string, projectId?: string): Observable<InstanceInfo> {
    const body: Record<string, string> = { 
      agent_id: agentId, 
    };
    if (instanceId) {
      body.instance_id = instanceId;
    }
    if (projectId) {
      body.project_id = projectId;
    }
    return this.http.post<InstanceInfo>(`${this.API_BASE}/instances`, body);
  }

  listInstances(limit: number = 100, offset: number = 0, projectId?: string): Observable<InstanceListResponse> {
    let params = new HttpParams()
      .set('limit', limit.toString())
      .set('offset', offset.toString());
    if (projectId) {
      params = params.set('project_id', projectId);
    }
    return this.http.get<InstanceListResponse>(`${this.API_BASE}/instances`, { params });
  }

  getInstance(instanceId: string): Observable<InstanceInfo> {
    return this.http.get<InstanceInfo>(`${this.API_BASE}/instances/${instanceId}`);
  }

  deleteInstance(instanceId: string): Observable<{ terminated: boolean }> {
    return this.http.delete<{ terminated: boolean }>(`${this.API_BASE}/instances/${instanceId}`);
  }

  stopInstance(instanceId: string): Observable<{ stopped: boolean; cancelled_requests: number }> {
    return this.http.post<{ stopped: boolean; cancelled_requests: number }>(`${this.API_BASE}/instances/${instanceId}/stop`, {});
  }

  // Messages
  sendMessage(instanceId: string, content: string, images?: string[]): Observable<MessageResponse> {
    const body = images?.length ? { content, images } : { content };
    return this.http.post<MessageResponse>(`${this.API_BASE}/instances/${instanceId}/messages`, body);
  }

  getMessages(instanceId: string): Observable<Message[]> {
    return this.http.get<Message[]>(`${this.API_BASE}/instances/${instanceId}/messages`);
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
  listMappings(sourceId: string): Observable<InstanceMappingListResponse> {
    return this.http.get<InstanceMappingListResponse>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}/mappings`);
  }

  createMapping(sourceId: string, mapping: InstanceMappingCreate): Observable<InstanceMapping> {
    return this.http.post<InstanceMapping>(`${this.API_BASE}/sources/${encodeURIComponent(sourceId)}/mappings`, mapping);
  }

  deleteMapping(sourceId: string, mappingId: string): Observable<DeleteResponse> {
    return this.http.delete<DeleteResponse>(`${this.API_BASE}/sources/${sourceId}/mappings/${encodeURIComponent(mappingId)}`);
  }
}
