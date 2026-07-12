import { Injectable, NgZone, signal } from '@angular/core';
import type { Message, SSEEvent, ToolCall, InstanceInfo } from '../models';
import { ApiService } from './api.service';

export interface SubTask {
  id: string;
  text: string;
  status: 'pending' | 'done';
}

/**
 * Pending user-message injection payload. The backend writes this to the
 * per-instance RAM slot when a send_message hits a RUNNING / WAITING_CHILDREN
 * instance (Phase 2 / Task 3) and clears it on consumption / pause. The SSE
 * ``injection_pending`` event carries the same shape inside the ``message``
 * field; the GET fallback endpoint returns it as a flat object.
 */
export interface InjectionEvent {
  instance_id: string;
  event_type: string;
  content: string | null;
  timestamp: string;
}

export interface TodoNode {
  id: string;
  index: number;  // PRESERVED for backward compat — always present in SSE payload
  text: string;
  status: 'pending' | 'in_progress' | 'done';
  comment: string;
  next_ids: string[];
  subtasks: SubTask[];
}

export type TodoItem = TodoNode;

@Injectable({
  providedIn: 'root'
})
export class SseService {
  private readonly API_BASE = '/api';
  
  private eventSource: EventSource | null = null;
  private currentInstanceId: string | null = null;

  // Signals for reactive state
  isStreaming = signal(false);
  events = signal<SSEEvent[]>([]);
  latestError = signal<{ message: string; instance_id?: string } | null>(null);
  
  // Messages from checkpoint events - replaces messageDeltas
  messages = signal<Message[]>([]);

  // Status change events for instance updates
  statusChange = signal<{ instance_id: string; status: string; agent_id?: string } | null>(null);

  // Instance created events for tree updates (queue to handle rapid spawning)
  instanceCreatedQueue = signal<InstanceInfo[]>([]);

  // Latest context-usage snapshot for the connected instance. The backend
  // emits this on SSE connect, on every user/assistant turn boundary, and
  // after each LangGraph checkpoint update. The header indicator binds
  // to this signal.
  contextUsage = signal<{
    tokens: number;
    context_window: number;
    percent: number;
    model_name: string;
  } | null>(null);

  // Single-instance todo list. The frontend only ever displays one chat
  // at a time, so we keep one signal and overwrite it from todo_update.
  todos = signal<TodoNode[]>([]);

  // Pending user-message injection for the connected instance. Set by the
  // ``injection_pending`` SSE event and the GET /api/instances/{id}/injection
  // fallback, cleared by ``injection_consumed`` / ``injection_cleared``. The
  // chat component binds to this signal to surface the "queued" indicator
  // and pre-fill the composer for cancellation / edit.
  pendingInjection = signal<InjectionEvent | null>(null);

  // Pending tool_result outputs keyed by tool_call_id. Flushed whenever a
  // matching tool_call or assistant_message arrives, so a tool_result that
  // races ahead of its tool_call is not lost. Cleared on disconnect.
  private pendingToolOutputs = new Map<string, string>();

  constructor(private ngZone: NgZone, private api: ApiService) {}

  /**
   * Append or update a message in the list with deduplication by message_id.
   * Messages are sorted by created_at to maintain correct chronological order.
   * Also flushes any pending tool_result outputs that match tool_calls in the
   * upserted message, then clears the buffer for the consumed tool_call_ids.
   */
  private upsertMessage(message: Message): void {
    if (message.tool_calls && message.tool_calls.length > 0) {
      for (const tc of message.tool_calls) {
        if (this.pendingToolOutputs.has(tc.id)) {
          tc.output = this.pendingToolOutputs.get(tc.id);
          this.pendingToolOutputs.delete(tc.id);
        }
      }
    }
    this.messages.update(msgs => {
      const existsIndex = msgs.findIndex(m => m.message_id === message.message_id);
      let result: Message[];
      if (existsIndex >= 0) {
        result = [...msgs];
        result[existsIndex] = message;
      } else {
        result = [...msgs, message];
      }
      // Sort by created_at to maintain correct chronological order
      result.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
      return result;
    });
  }

  /**
   * Patch the output of a tool call in place across the message list.
   * Looks for the first assistant message containing a tool_calls entry with
   * the given id and updates its `output` field. If no match is found, the
   * output is buffered and applied to the next matching upsert.
   */
  private patchToolCallOutput(toolCallId: string, output: string): void {
    let matched = false;
    this.messages.update(msgs => {
      const idx = msgs.findIndex(
        m => m.tool_calls?.some(tc => tc.id === toolCallId)
      );
      if (idx < 0) return msgs;
      matched = true;
      const target = msgs[idx];
      const toolCalls = target.tool_calls!.map(tc =>
        tc.id === toolCallId ? { ...tc, output } : tc
      );
      const result = msgs.slice();
      result[idx] = { ...target, tool_calls: toolCalls };
      return result;
    });
    if (!matched) {
      this.pendingToolOutputs.set(toolCallId, output);
    } else {
      this.pendingToolOutputs.delete(toolCallId);
    }
  }

  /**
   * Map raw SSE message data to Message type.
   */
  private mapToMessage(data: Record<string, unknown>): Message {
    return {
      message_id: data['message_id'] as string,
      role: (data['role'] as 'user' | 'assistant' | 'system' | 'tool') || 'assistant',
      content: (data['content'] as string) || '',
      thinking: (data['thinking'] as string | null) || null,
      thinking_extracted: (data['thinking_extracted'] as string | null) || null,
      tool_calls: Array.isArray(data['tool_calls']) ? data['tool_calls'] as ToolCall[] : undefined,
      created_at: (data['created_at'] as string) || new Date().toISOString(),
      instance_id: data['instance_id'] as string | undefined,
      images: Array.isArray(data['images']) 
        ? (data['images'] as string[]).filter((img: unknown) => typeof img === 'string' && img.startsWith('data:image/'))
        : undefined,
    };
  }

  /**
   * Connects to SSE stream for the specified instance.
   */
  connect(instanceId: string): void {
    console.log('[SSE] connect() called with instanceId:', instanceId);
    if (this.currentInstanceId === instanceId && this.eventSource) {
      console.log('[SSE] Already connected to this instance');
      return;
    }

    this.disconnect();
    this.currentInstanceId = instanceId;
    this.clearEvents();
    this.connectInternal();

    // Reconcile pending injection state from the REST fallback. SSE may
    // have missed events during a disconnect window (e.g. the user
    // navigated away while an injection was queued); this catches the
    // backend's current truth before any pending event arrives. The GET
    // endpoint is idempotent — no subscription, no polling loop.
    this.fetchPendingInjection(instanceId);
  }

  private connectInternal(): void {
    console.log('[SSE] connectInternal() called');
    if (!this.currentInstanceId) return;

    if (this.eventSource) {
      return;
    }

    const url = `${this.API_BASE}/instances/${this.currentInstanceId}/events`;
    console.log('[SSE] Creating EventSource with URL:', url);
    const eventSource = new EventSource(url);
    this.eventSource = eventSource;

    // Connected
    eventSource.addEventListener('connected', (e: MessageEvent) => {
      this.ngZone.run(() => {
        console.log('[SSE] Connected to instance:', this.currentInstanceId);
        this.isStreaming.set(true);
        try {
          const data = JSON.parse(e.data);
          this.events.update(evts => [...evts, { type: 'connected', data }]);
        } catch {
          this.events.update(evts => [...evts, { type: 'connected', data: {} }]);
        }
      });
    });

    // Individual message events
    eventSource.addEventListener('user_message', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const message = this.mapToMessage(data.message);
          this.upsertMessage(message);
          this.events.update(evts => [...evts, { type: 'user_message', data }]);
        } catch (err) {
          console.error('[SSE] Failed to parse user_message:', err);
        }
      });
    });

    eventSource.addEventListener('assistant_message', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const message = this.mapToMessage(data.message);
          this.upsertMessage(message);
          this.events.update(evts => [...evts, { type: 'assistant_message', data }]);
        } catch (err) {
          console.error('[SSE] Failed to parse assistant_message:', err);
        }
      });
    });

    eventSource.addEventListener('thinking', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const message = this.mapToMessage(data.message);
          this.upsertMessage(message);
        } catch (err) {
          console.error('[SSE] Failed to parse thinking:', err);
        }
      });
    });

    eventSource.addEventListener('tool_call', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const message = this.mapToMessage(data.message);
          this.upsertMessage(message);
          this.events.update(evts => [...evts, { type: 'tool_call', data }]);
        } catch (err) {
          console.error('[SSE] Failed to parse tool_call:', err);
        }
      });
    });

    // Real-time tool result — patches the matching tool_calls[i].output in place
    eventSource.addEventListener('tool_result', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const msg = data.message as {
            tool_call_id?: string;
            content?: string;
            message_id?: string;
          };
          if (msg.tool_call_id && typeof msg.content === 'string') {
            this.patchToolCallOutput(msg.tool_call_id, msg.content);
          }
          this.events.update(evts => [...evts, { type: 'tool_result', data }]);
        } catch (err) {
          console.error('[SSE] Failed to parse tool_result:', err);
        }
      });
    });

    // Status change event
    eventSource.addEventListener('status_change', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          console.log('[SSE] status_change event:', data);
          this.events.update(evts => [...evts, { type: 'status_change', data }]);
          this.statusChange.set({
            instance_id: data.instance_id as string,
            status: data.status as string,
            agent_id: data.agent_id as string | undefined,
          });
        } catch (err) {
          console.error('[SSE] Failed to parse status_change:', err);
        }
      });
    });

    // Instance created event
    eventSource.addEventListener('instance_created', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          console.log('[SSE] instance_created event:', data);
          this.events.update(evts => [...evts, { type: 'instance_created', data }]);
          // The instance data is nested in data.data
          const instanceData: InstanceInfo = {
            instance_id: data.data.instance_id as string,
            agent_id: data.data.agent_id as string,
            parent_id: (data.data.parent_id as string) || null,
            status: data.data.status as InstanceInfo['status'],
            project_id: data.data.project_id as string | null,
            title: data.data.title as string | null,
            children: (data.data.children as string[]) || [],
            created_at: data.data.created_at as string,
            updated_at: data.data.created_at as string,
          };
          this.instanceCreatedQueue.update(q => [...q, instanceData]);
        } catch (err) {
          console.error('[SSE] Failed to parse instance_created:', err);
        }
      });
    });

    // Context-usage snapshot. Backend emits this on SSE connect, before
    // every user turn, and after each LangGraph checkpoint update. The
    // payload is small ({tokens, context_window, percent, model_name}).
    eventSource.addEventListener('context_usage', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          this.contextUsage.set({
            tokens: Number(data.tokens),
            context_window: Number(data.context_window),
            percent: Number(data.percent),
            model_name: String(data.model_name ?? ''),
          });
        } catch (err) {
          console.error('[SSE] Failed to parse context_usage:', err);
        }
      });
    });

    // Todo update event - replaces the todo list for the active instance.
    // Placed inline alongside the other event listeners to keep all SSE
    // wiring colocated in connectInternal().
    eventSource.addEventListener('todo_update', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          // Defensively default subtasks to [] so older payloads and partial
          // objects render without runtime errors in the UI templates.
          const todos = (data.todos ?? []).map((t: any) => ({
            ...t,
            subtasks: t.subtasks ?? [],
          }));
          this.todos.set(todos);
        } catch (err) {
          console.error('[SSE] Failed to parse todo_update:', err);
        }
      });
    });

    // Injection lifecycle events. ``injection_pending`` carries the full
    // content + timestamp inside the ``message`` field (the SSE envelope
    // wraps it). ``injection_consumed`` and ``injection_cleared`` clear the
    // slot — both are no-ops on the signal side, so we share the same
    // handler shape. See daemon/routers/messages.py:_emit_injection_sse.
    eventSource.addEventListener('injection_pending', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const message = data.message ?? {};
          this.pendingInjection.set({
            instance_id: data.instance_id as string,
            event_type: data.event_type as string,
            content: (message.content as string | null) ?? null,
            timestamp: (message.timestamp as string) ?? '',
          });
        } catch (err) {
          console.error('[SSE] Failed to parse injection_pending:', err);
        }
      });
    });

    eventSource.addEventListener('injection_consumed', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          console.log('[SSE] injection_consumed event:', data);
          this.pendingInjection.set(null);
        } catch (err) {
          console.error('[SSE] Failed to parse injection_consumed:', err);
        }
      });
    });

    eventSource.addEventListener('injection_cleared', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          console.log('[SSE] injection_cleared event:', data);
          this.pendingInjection.set(null);
        } catch (err) {
          console.error('[SSE] Failed to parse injection_cleared:', err);
        }
      });
    });

    // Error event
    eventSource.addEventListener('error', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          console.error('[SSE] error event:', data);
          
          this.events.update(evts => [...evts, { type: 'error', data }]);
          this.isStreaming.set(false);
          
          if (data.error) {
            this.latestError.set({
              message: String(data.error),
              instance_id: data.instance_id || this.currentInstanceId || undefined,
            });
          }
        } catch {
          // If we can't parse, it's a connection error
          console.error('[SSE] Connection error');
          this.handleClose();
        }
      });
    });

    // Keepalive
    eventSource.addEventListener('keepalive', () => {
      // Connection is alive, no action needed
    });

    // EventSource error handler (connection-level errors)
    eventSource.onerror = () => {
      console.error('[SSE] EventSource connection error');
      this.handleClose();
    };
  }

  /**
   * Handle disconnect/close - reset streaming state.
   */
  private handleClose(): void {
    this.isStreaming.set(false);
  }

  disconnect(): void {
    console.log('[SSE] disconnect() called');
    
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    
    this.currentInstanceId = null;
    this.isStreaming.set(false);
  }

  /**
   * Fetch the current pending injection for ``instanceId`` via the REST
   * fallback endpoint and seed the ``pendingInjection`` signal. Called
   * from ``connect()`` to reconcile state on initial chat load and after
   * SSE reconnection — SSE itself drives real-time updates via the
   * ``injection_pending`` / ``injection_consumed`` / ``injection_cleared``
   * event listeners. Errors are logged and swallowed: a missing or
   * 404'd endpoint must not break the chat UI.
   */
  fetchPendingInjection(instanceId: string): void {
    this.api.getPendingInjection(instanceId).subscribe({
      next: (resp) => {
        if (resp.pending && resp.content !== null) {
          this.pendingInjection.set({
            instance_id: instanceId,
            event_type: 'injection_pending',
            content: resp.content,
            timestamp: resp.timestamp ?? '',
          });
        } else {
          this.pendingInjection.set(null);
        }
      },
      error: (err) => {
        console.error('[SSE] Failed to fetch pending injection:', err);
      },
    });
  }

  /**
   * Clears all event-related state.
   *
   * Note: ``todos`` is intentionally NOT cleared here. The todos signal is
   * managed independently by the chat component (REST ``getTodos`` on
   * instance load) and by the SSE ``todo_update`` handler (live mutations).
   * Wiping it as part of the SSE connect lifecycle causes a race where
   * freshly-loaded todos get erased by ``connect() -> clearEvents()`` right
   * after ``loadInstanceMessages`` populates them. The next todo mutation
   * (or a re-fetch) repopulates the signal as needed.
   */
  clearEvents(): void {
    this.events.set([]);
    this.latestError.set(null);
    this.messages.set([]);
    this.statusChange.set(null);
    this.instanceCreatedQueue.set([]);
    this.contextUsage.set(null);
    this.pendingInjection.set(null);
    this.pendingToolOutputs.clear();
  }
}
