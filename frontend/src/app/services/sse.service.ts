import { Injectable, NgZone, signal } from '@angular/core';
import type { Message, SSEEvent, ToolCall, InstanceInfo } from '../models';
import type { QuestionPack } from '../models/question.model';
import { ApiService } from './api.service';
import { isTerminalStatus } from './message-merge.util';

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
 *
 * Append-list semantics (Phase 2 / Task 3+): the backend now accumulates
 * multiple queued injections on a single instance and emits one
 * ``injection_pending`` event per appended message, each carrying the
 * running ``pending_count``. ``injection_consumed`` is emitted exactly once
 * when the agent drains the queue, and clears the slot wholesale.
 */
export interface InjectionEvent {
  instance_id: string;
  event_type: string;
  content: string | null;
  timestamp: string;
  /** Total queued injections on this instance after the latest append. */
  pending_count: number;
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

  // Pending question pack for the connected instance (Phase 4 / Question
  // Tool). Set by the ``question_pack`` SSE event whenever the agent emits
  // a pending question; cleared to ``null`` on instance switch so a late
  // event from a previous instance never re-opens the wizard.
  //
  // Visibility is driven ENTIRELY by this signal — NOT by ``status_change``
  // (F3). The pause cascade cancels the graph task mid-execution, so the
  // ``status_change`` → paused event may never fire. The
  // ``question_pack`` event is emitted by the tool itself before the
  // cascade and is the only reliable pause-UI signal for this state.
  questionPack = signal<QuestionPack | null>(null);

  // Reconnect-refetch trigger (message-display-latency §4.3 item 10).
  // Bumped each time the SSE channel observes an error/disconnect followed
  // by a fresh ``connected`` event. The chat component listens to this
  // signal and runs a one-shot merge-mode ``loadInstanceMessages`` to
  // catch up on any messages that landed while the channel was down
  // (LiveEventHub is fire-and-forget live-only — no replay buffer).
  //
  // Idempotent: a single ``connected`` event after a disconnect bumps the
  // counter exactly once; subsequent ``connected`` events without an
  // intervening error do NOT bump (no refetch loop).
  refetchRequest = signal<number>(0);

  // Pending-purge trigger (message-display-latency §4.3 item 11 second
  // half). Bumped when a ``status_change`` lands with a terminal status
  // (completed / error / terminated / failed). The chat component listens
  // to this and drops its provisional pending entries — they cannot
  // possibly resolve once the instance has shut down. Bumping a counter
  // (rather than emitting a typed event) keeps the surface minimal and
  // matches the reconnect-refetch trigger pattern.
  pendingPurgeRequest = signal<number>(0);

  // Pending tool_result outputs keyed by tool_call_id. Flushed whenever a
  // matching tool_call or assistant_message arrives, so a tool_result that
  // races ahead of its tool_call is not lost. Cleared on disconnect.
  private pendingToolOutputs = new Map<string, string>();

  /**
   * True after the current EventSource observed a connection-level error
   * (``onerror`` / ``handleClose``). Reset to ``false`` the next time a
   * fresh ``connected`` event lands — that transition is the
   * reconnect-refetch trigger (message-display-latency §4.3 item 10).
   *
   * Not exposed: it's a private latch the connected-handler reads. The
   * chat component subscribes to ``refetchRequest`` instead.
   */
  private connectionHadError = false;

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

        // Reconnect catch-up (message-display-latency §4.3 item 10): if the
        // channel saw an error/disconnect earlier in this connection
        // lifecycle, bump the refetch request exactly once. The chat
        // component listens and triggers a merge-mode REST refetch — the
        // union-by-id merge in ``loadInstanceMessages`` is idempotent so
        // duplicate SSE echoes and refetched rows collapse onto the same
        // bubbles. Without an intervening error, this is a no-op so a
        // stable connection never loops refetches.
        if (this.connectionHadError) {
          this.connectionHadError = false;
          this.refetchRequest.update(n => n + 1);
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
          // Pending-purge trigger (message-display-latency §4.3 item 11).
          // Terminal statuses (completed / error / terminated / failed)
          // mean the instance cannot possibly consume any provisional
          // pending entry, so the chat component clears them in its
          // own message list. We delegate the actual mutation to the
          // chat component because it owns the visible ``messages``
          // signal (the SSE-side ``messages`` mirror only holds live
          // SSE echoes — it never has provisional entries to begin
          // with). Bumping a counter is the same trigger pattern as
          // ``refetchRequest`` and keeps the surface minimal.
          if (isTerminalStatus(data.status as string | null)) {
            this.pendingPurgeRequest.update(n => n + 1);
          }
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

    // Question pack event (Phase 4 / Question Tool). Carries the full
    // QuestionPack inside ``data.message`` (status='pending',
    // 'answered', or 'dismissed'). Visibility is driven entirely by this
    // signal — see the ``questionPack`` declaration for the F3 rationale
    // (the pause cascade swallows the status_change→paused event, so the
    // wizard cannot rely on it). On 'answered' the frontend wizard
    // auto-hides via the ``status === 'pending'`` check; we still store
    // the answered pack briefly so a re-render doesn't blank the answer
    // list, then drop it on the next clearEvents() cycle. On 'dismissed'
    // we null the signal outright so the dismissed state is unambiguous
    // (no possibility of a stale answered-style payload re-populating the
    // wizard mid-dismiss).
    eventSource.addEventListener('question_pack', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const pack = (data.message ?? null) as QuestionPack | null;
          if (pack && pack.status === 'dismissed') {
            this.questionPack.set(null);
          } else {
            this.questionPack.set(pack);
          }
        } catch (err) {
          console.error('[SSE] Failed to parse question_pack:', err);
        }
      });
    });

    // Injection lifecycle events. ``injection_pending`` is emitted on EVERY
    // append under append-list semantics — the payload carries a running
    // ``pending_count`` so the UI can render "N pending" without us having
    // to track the count locally. We still keep the latest content /
    // timestamp so the existing pending-injection card can show what the
    // most recent queued message is.
    //
    // ``injection_consumed`` fires once when the agent drains the queue
    // and clears the slot. ``injection_cleared`` is retained for backward
    // compatibility with older backends (the route no longer emits it,
    // but keeping the handler is harmless and the no-op set(null) matches
    // the steady-state signal value). See
    // daemon/routers/messages.py:_emit_injection_sse.
    eventSource.addEventListener('injection_pending', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const message = data.message ?? {};
          // ``pending_count`` is the authoritative running total from the
          // backend. Older backends that don't ship the field fall back to
          // ``1`` so the existing UI keeps working (single-slot semantics).
          const rawCount = message.pending_count;
          const pending_count =
            typeof rawCount === 'number' && Number.isFinite(rawCount) && rawCount > 0
              ? Math.floor(rawCount)
              : 1;
          this.pendingInjection.set({
            instance_id: data.instance_id as string,
            event_type: data.event_type as string,
            content: (message.content as string | null) ?? null,
            timestamp: (message.timestamp as string) ?? '',
            pending_count,
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
      // Reconnect catch-up latch (message-display-latency §4.3 item 10):
      // mark the connection as having observed an error so the next
      // ``connected`` event triggers exactly one merge-mode refetch.
      this.connectionHadError = true;
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
   * from the chat load flow to reconcile state on initial load and instance
   * switches — SSE itself drives real-time updates via the
   * ``injection_pending`` / ``injection_consumed`` / ``injection_cleared``
   * event listeners. Errors are logged and swallowed: a missing or
   * 404'd endpoint must not break the chat UI.
   */
  fetchPendingInjection(instanceId: string): void {
    this.api.getPendingInjection(instanceId).subscribe({
      next: (resp) => {
        if (this.currentInstanceId !== instanceId) return;
        if (resp.pending && resp.content !== null) {
          this.pendingInjection.set({
            instance_id: instanceId,
            event_type: 'injection_pending',
            content: resp.content,
            timestamp: resp.timestamp ?? '',
            // REST fallback returns a flat view of the slot — under
            // append-list semantics we don't get an authoritative count
            // here, so seed it as ``1``. The next ``injection_pending``
            // SSE event (if any) will reconcile the count from the
            // backend's running total.
            pending_count: 1,
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
   * Fetch the current pending question pack for ``instanceId`` via the REST
   * fallback endpoint and seed the ``questionPack`` signal. Called from the
   * chat load flow to reconcile state on initial load and instance switches —
   * SSE itself drives real-time updates via the ``question_pack`` event
   * listener. The underlying ApiService Observable logs HTTP errors and recovers
   * with catchError→of(null), so the signal is set to null when the endpoint fails.
   */
  fetchPendingQuestion(instanceId: string): void {
    this.api.fetchPendingQuestion(instanceId).subscribe((pack) => {
      if (this.currentInstanceId !== instanceId) return;
      this.questionPack.set(pack);
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
    this.questionPack.set(null);
    this.pendingToolOutputs.clear();
    // Reconnect catch-up latch + purge trigger counters — reset on
    // disconnect so a brand-new connection cycle starts clean. The
    // triggers themselves (``refetchRequest`` / ``pendingPurgeRequest``)
    // are NOT reset here: the chat component subscribes to them via
    // effects and must still observe any pending value across a
    // connect/disconnect cycle (otherwise a purge scheduled just
    // before disconnect would silently disappear).
    this.connectionHadError = false;
  }
}
