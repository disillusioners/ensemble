import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import type { TodoItem, TodoNode, SubTask } from './sse.service';
import type { QuestionPack } from '../models/question.model';
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
  DeleteResponse,
  PauseResponse,
  ResumeResponse
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
      body['instance_id'] = instanceId;
    }
    if (projectId) {
      body['project_id'] = projectId;
    }
    return this.http.post<InstanceInfo>(`${this.API_BASE}/instances`, body);
  }

  listInstances(limit: number = 100, offset: number = 0, projectId?: string, excludeKb: boolean = true): Observable<InstanceListResponse> {
    let params = new HttpParams()
      .set('limit', limit.toString())
      .set('offset', offset.toString())
      .set('exclude_kb', excludeKb.toString());
    if (projectId) {
      params = params.set('project_id', projectId);
    }
    return this.http.get<InstanceListResponse>(`${this.API_BASE}/instances`, { params });
  }

  getInstance(instanceId: string): Observable<InstanceInfo> {
    return this.http.get<InstanceInfo>(`${this.API_BASE}/instances/${instanceId}`);
  }

  /**
   * Delete an instance.
   *
   * By default this performs a soft terminate (status -> terminated). Pass
   * ``hardDelete = true`` to ask the backend to permanently remove the
   * instance row from the database.
   *
   * The backend accepts a ``hard_delete`` query parameter; when the
   * parameter is missing or ``false`` the call is equivalent to a
   * terminate-only flow.
   */
  deleteInstance(instanceId: string, hardDelete: boolean = false): Observable<{ terminated: boolean }> {
    const params = new HttpParams().set('hard_delete', hardDelete.toString());
    return this.http.delete<{ terminated: boolean }>(`${this.API_BASE}/instances/${instanceId}`, { params });
  }

  pauseInstance(instanceId: string): Observable<PauseResponse> {
    return this.http.post<PauseResponse>(`${this.API_BASE}/instances/${instanceId}/pause`, {});
  }

  resumeInstance(instanceId: string, message?: string): Observable<ResumeResponse> {
    return this.http.post<ResumeResponse>(`${this.API_BASE}/instances/${instanceId}/resume`, {
      message: message || null
    });
  }

  // Messages
  sendMessage(instanceId: string, content: string, images?: string[]): Observable<MessageResponse> {
    const body = images?.length ? { content, images } : { content };
    return this.http.post<MessageResponse>(`${this.API_BASE}/instances/${instanceId}/messages`, body);
  }

  getMessages(instanceId: string): Observable<Message[]> {
    return this.http.get<Message[]>(`${this.API_BASE}/instances/${instanceId}/messages`);
  }

  // Todos
  getTodos(instanceId: string): Observable<TodoNode[]> {
    return this.http.get<TodoNode[]>(`${this.API_BASE}/instances/${instanceId}/todos`);
  }

  setTodoComment(instanceId: string, nodeId: string, comment: string): Observable<TodoNode> {
    return this.http.post<TodoNode>(`${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/comment`, { comment });
  }

  addTodoEdge(instanceId: string, fromId: string, toId: string): Observable<any> {
    return this.http.post(
      `${this.API_BASE}/instances/${instanceId}/todos/edges`,
      { from_id: fromId, to_id: toId }
    );
  }

  removeTodoEdge(instanceId: string, fromId: string, toId: string): Observable<any> {
    return this.http.request(
      'DELETE',
      `${this.API_BASE}/instances/${instanceId}/todos/edges`,
      { body: { from_id: fromId, to_id: toId } }
    );
  }

  // Sub-tasks: add / update / remove. All return the freshly-rendered todo
  // list so the caller can adopt the server's ordering / status as the
  // source of truth (subtask mutations are NON-OPTIMISTIC in the UI).
  addSubtask(
    instanceId: string,
    nodeId: string,
    text: string,
  ): Observable<{ todos: TodoNode[]; reminder: string }> {
    return this.http.post<{ todos: TodoNode[]; reminder: string }>(
      `${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/subtasks`,
      { text },
    );
  }

  updateSubtask(
    instanceId: string,
    nodeId: string,
    subtaskId: string,
    status: string,
    autoComplete: boolean,
  ): Observable<{ todos: TodoNode[]; reminder: string; auto_completed: boolean }> {
    return this.http.patch<{ todos: TodoNode[]; reminder: string; auto_completed: boolean }>(
      `${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/subtasks/${subtaskId}`,
      { status, auto_complete: autoComplete },
    );
  }

  removeSubtask(
    instanceId: string,
    nodeId: string,
    subtaskId: string,
  ): Observable<{ todos: TodoNode[]; reminder: string }> {
    return this.http.delete<{ todos: TodoNode[]; reminder: string }>(
      `${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/subtasks/${subtaskId}`,
    );
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

  // Injection slot (Phase 2 / Task 6 fallback). Returns the backend's
  // current view of the per-instance RAM injection slot so the chat UI
  // can reconcile on initial load and SSE reconnect. ``pending=false`` is
  // a valid steady state (no injection queued) — not an error.
  getPendingInjection(instanceId: string): Observable<{
    pending: boolean;
    content: string | null;
    timestamp: string | null;
  }> {
    return this.http.get<{
      pending: boolean;
      content: string | null;
      timestamp: string | null;
    }>(`${this.API_BASE}/instances/${instanceId}/injection`);
  }

  /**
   * Submit answers to a pending question pack. POST /api/instances/{id}/answer.
   * The backend will resume the paused instance with the answers converted into
   * a HumanMessage. The frontend relies on the `question_pack` SSE event with
   * status='answered' to hide the wizard — not on the HTTP response.
   */
  answerQuestions(instanceId: string, answers: Record<string, string>): Observable<QuestionPack> {
    return this.http.post<QuestionPack>(
      `${this.API_BASE}/instances/${instanceId}/answer`,
      { answers }
    );
  }

}
