import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, of, throwError, firstValueFrom } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
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
  ResumeResponse,
  JobQueueListResponse,
  CommandAck,
  GetActiveResponse
} from '../models';

// ─────────────────────────────────────────────────────────────────────────
// Slash-command send-path helpers (Phase 2 / Task 2). Pure functions so the
// wire contract has an executable Jest spec (parse-command-ack.spec.ts)
// without TestBed — logic-mirror house style.
// ─────────────────────────────────────────────────────────────────────────

/** HTTP 400 ``UNKNOWN_COMMAND`` mapped to a typed error carrying the
 *  available-commands list (§7 split rule / O13). The FE toast path
 *  consumes ``available``; it later feeds slash autocomplete without a
 *  contract change. */
export class UnknownCommandHttpError extends Error {
  readonly code = 'UNKNOWN_COMMAND' as const;
  readonly available: string[];
  readonly serverMessage: string;

  constructor(available: string[], serverMessage = 'Unknown command') {
    super(serverMessage);
    this.name = 'UnknownCommandHttpError';
    this.available = available;
    this.serverMessage = serverMessage;
  }
}

/** Discriminated parse of the POST /messages response body. The command
 *  intercept (Phase 1) returns ``{status: 'command', ...}``; every legacy
 *  body (202 ``injected``, 200 auto-resume, 200 enqueue — the latter two
 *  carry NO ``status`` key) is a message response. */
export type ParsedSendResponse =
  | { kind: 'message'; message: MessageResponse }
  | { kind: 'command'; ack: CommandAck };

/**
 * THE single parsing point for the POST /messages response (phase2 Task 2).
 * Encodes the pinned §7 CommandAck shape exactly — the Jest adapter test
 * (parse-command-ack.spec.ts) is the executable contract spec: a Phase 1
 * wire drift fails here with a named field (R6).
 *
 * Discrimination is defensive (``payload?.status === 'command'``) because
 * legacy message bodies do NOT all carry a ``status`` key (the PAUSED
 * auto-resume 200 and the IDLE enqueue 200 ship none).
 */
export function parseCommandAck(payload: unknown): ParsedSendResponse {
  if (
    payload !== null &&
    typeof payload === 'object' &&
    (payload as { status?: unknown }).status === 'command'
  ) {
    return { kind: 'command', ack: payload as CommandAck };
  }
  return { kind: 'message', message: payload as MessageResponse };
}

/**
 * Inspect an HttpClient error and map the HTTP 400 ``UNKNOWN_COMMAND``
 * validation envelope to a typed {@link UnknownCommandHttpError}. Returns
 * ``null`` for every other error so callers can rethrow unchanged.
 *
 * Backend wire shape (daemon/routers/messages.py:256-267 +
 * daemon/models/common.py ErrorResponse): FastAPI wraps the ErrorResponse
 * under ``detail``, and the available list rides the ADDITIVE ``details``
 * dict — ``err.error.detail = {code, message, details: {available: [...]}}``.
 */
export function extractUnknownCommandError(err: unknown): UnknownCommandHttpError | null {
  // Idempotent: an already-typed error (the sendMessage pipe maps it once;
  // sendCommand's error handler inspects the result) passes through so a
  // second inspection cannot degrade it to the generic error branch.
  if (err instanceof UnknownCommandHttpError) return err;
  const httpErr = err as {
    status?: number;
    error?: {
      detail?: {
        code?: string;
        message?: string;
        details?: { available?: unknown };
      };
    };
  } | null;
  if (!httpErr || httpErr.status !== 400) return null;
  const detail = httpErr.error?.detail;
  if (!detail || detail.code !== 'UNKNOWN_COMMAND') return null;
  const rawAvailable = detail.details?.available;
  const available = Array.isArray(rawAvailable)
    ? rawAvailable.filter((v): v is string => typeof v === 'string')
    : [];
  return new UnknownCommandHttpError(available, detail.message || 'Unknown command');
}

/** True when ``ack`` seeded a real command run (BE ships ``command_id:
 *  null`` on rejected acks — nothing to correlate). */
export function isAcceptedCommandAck(ack: CommandAck): ack is CommandAck & { command_id: string } {
  return ack.state === 'accepted' && typeof ack.command_id === 'string' && ack.command_id.length > 0;
}

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
  createInstance(agentId: string, instanceId?: string, projectId?: string, versionTag?: string): Observable<InstanceInfo> {
    const body: Record<string, string> = {
      agent_id: agentId,
    };
    if (instanceId) {
      body['instance_id'] = instanceId;
    }
    if (projectId) {
      body['project_id'] = projectId;
    }
    // Backend Phase 2 — only include version_tag when explicitly provided.
    // Empty string would round-trip as a "version_tag=''" entry in the DB.
    if (versionTag) {
      body['version_tag'] = versionTag;
    }
    return this.http.post<InstanceInfo>(`${this.API_BASE}/instances`, body);
  }

  listInstances(limit: number = 100, offset: number = 0, projectId?: string, excludeKb: boolean = true, search?: string): Observable<InstanceListResponse> {
    let params = new HttpParams()
      .set('limit', limit.toString())
      .set('offset', offset.toString())
      .set('exclude_kb', excludeKb.toString());
    if (projectId) {
      params = params.set('project_id', projectId);
    }
    if (search && search.trim().length > 0) {
      params = params.set('search', search.trim());
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

  // Watchover — toggle security monitoring on/off for an instance.
  //
  // Body shape is { enabled, requirement, context, resume_message,
  // next_command }.
  //   * ``resume_message`` is forwarded to the watchover activation
  //     lifecycle so the target instance receives the operator-supplied
  //     message on the post-activation resume (or "continue" when null).
  //   * ``next_command`` is the explicit "next command" captured by the
  //     ChatComponent watchover dialog when the instance is NOT in a
  //     running state. The backend forwards this as the resume message
  //     so the operator can both enable watchover and tell the agent
  //     what to do next in a single step. When the instance IS running
  //     the dialog is skipped and ``next_command`` is null — the
  //     intelligent context builder generates guardrails from the
  //     current message stream.
  // Both fields are sent as ``null`` when the caller does not supply
  // them so the backend contract is stable and the wiring is opt-in.
  setWatchover(
    instanceId: string,
    enabled: boolean,
    requirement?: string | null,
    nextCommand?: string | null,
  ): Observable<{ watchover_enabled: boolean; instance_id: string }> {
    return this.http.post<{ watchover_enabled: boolean; instance_id: string }>(
      `${this.API_BASE}/instances/${instanceId}/watchover`,
      {
        enabled,
        requirement: requirement ?? null,
        context: null,
        resume_message: null,
        next_command: nextCommand ?? null,
      }
    );
  }

  // UI preferences (pinned + color tag + icon tag).
  //
  // Body fields are independent: the caller may send only ``pinned``,
  // only ``color_tag``, only ``icon_tag``, or any combination. The backend
  // applies a partial update so any field omitted from the body keeps its
  // current value.
  updateInstanceUiPrefs(
    instanceId: string,
    body: { pinned?: boolean | null; color_tag?: string | null; icon_tag?: string | null },
  ): Observable<{
    instance_id: string;
    pinned: boolean | null;
    pinned_at: string | null;
    color_tag: string | null;
    icon_tag: string | null;
  }> {
    return this.http.put<{
      instance_id: string;
      pinned: boolean | null;
      pinned_at: string | null;
      color_tag: string | null;
      icon_tag: string | null;
    }>(`${this.API_BASE}/instances/${instanceId}/ui-prefs`, body);
  }

  // Clear all UI preferences (pinned + color_tag + icon_tag) for an instance.
  resetInstanceUiPrefs(instanceId: string): Observable<DeleteResponse> {
    return this.http.delete<DeleteResponse>(`${this.API_BASE}/instances/${instanceId}/ui-prefs`);
  }

  // Messages
  //
  // Phase 2 (slash-commands): the response is now a UNION. The Phase 1
  // BE-side intercept answers ``/command`` content with a sync CommandAck
  // (200) instead of a message body, and unknown commands with HTTP 400
  // ``UNKNOWN_COMMAND``. Discrimination happens in ``parseCommandAck``
  // (single parsing point — executable contract spec lives in
  // parse-command-ack.spec.ts), NOT here.
  sendMessage(
    instanceId: string,
    content: string,
    images?: string[],
    queueId?: string | null,
  ): Observable<MessageResponse | CommandAck> {
    const body: { content: string; images?: string[]; queue_id?: string } = { content };
    if (images?.length) body.images = images;
    if (queueId) body.queue_id = queueId;
    return this.http
      .post<MessageResponse | CommandAck>(`${this.API_BASE}/instances/${instanceId}/messages`, body)
      .pipe(
        catchError(err => {
          // Map HTTP 400 UNKNOWN_COMMAND to a typed error carrying
          // ``detail.available`` for the existing toast path. Every other
          // error rethrows UNCHANGED (normal-message path untouched).
          const unknown = extractUnknownCommandError(err);
          if (unknown) return throwError(() => unknown);
          return throwError(() => err);
        }),
      );
  }

  /**
   * GET /api/instances/{id}/commands/active — SSE-loss recovery fallback
   * (phase2 Task 8 / §7). NEVER throws: a network / HTTP failure resolves
   * to ``null`` (swallowed-error convention, sse.service.ts:653 pattern),
   * which the caller must treat as "no information" — keep the current
   * card, keep polling. A LEGITIMATE ``{exists: false}`` (daemon restart,
   * TTL expiry, disabled subsystem) is the authoritative silent-clear
   * signal and is returned verbatim.
   */
  getActiveCommand(instanceId: string): Promise<GetActiveResponse | null> {
    return firstValueFrom(
      this.http
        .get<GetActiveResponse>(`${this.API_BASE}/instances/${instanceId}/commands/active`)
        .pipe(
          catchError(err => {
            console.error('[Api] Failed to fetch active command:', err);
            return of(null);
          }),
        ),
    );
  }

  getQueues(projectId: string): Observable<JobQueueListResponse> {
    return this.http.get<JobQueueListResponse>(`${this.API_BASE}/projects/${projectId}/queues`);
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

  // Default agent versions
  getDefaultAgentVersions(): Observable<{ default_versions: Record<string, string | null> }> {
    return this.http.get<{ default_versions: Record<string, string | null> }>(
      `${this.API_BASE}/settings/default-agent-versions`,
    );
  }

  setDefaultAgentVersion(agentId: string, versionTag: string | null): Observable<any> {
    return this.http.put(
      `${this.API_BASE}/settings/default-agent-versions`,
      { agent_id: agentId, version_tag: versionTag },
    );
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

  // Question pack fallback (Phase 4 / Question Tool). GET /api/instances/{id}/question
  // returns the backend's current view of the per-instance question slot so the chat
  // UI can reconcile on initial load and instance switches. ``null`` is a valid steady
  // state (no question pending) — not an error. The Observable always emits a value
  // (pack or null) so callers don't need an error handler.
  fetchPendingQuestion(instanceId: string): Observable<QuestionPack | null> {
    return this.http
      .get<{ instance_id: string; question_pack: QuestionPack | null }>(
        `${this.API_BASE}/instances/${instanceId}/question`,
      )
      .pipe(
        map((res) => res.question_pack ?? null),
        catchError((err) => {
          console.error('[SSE] Failed to fetch pending question:', err);
          return of(null);
        }),
      );
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

  /**
   * Dismiss a pending question pack without answering. POST
   * /api/instances/{id}/question/dismiss. The backend will clear the
   * pending pack and resume the instance with a no-op signal so the
   * graph stops waiting on input. The frontend relies on the
   * `question_pack` SSE event with status='dismissed' to hide the wizard
   * — not on the HTTP response.
   */
  dismissQuestion(instanceId: string): Observable<QuestionPack> {
    return this.http.post<QuestionPack>(
      `${this.API_BASE}/instances/${instanceId}/question/dismiss`,
      {}
    );
  }

}
