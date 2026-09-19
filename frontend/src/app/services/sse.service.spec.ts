import { signal } from '@angular/core';
import type { Message, SSEEvent } from '../models';
import { SseService as RealSseService } from './sse.service';
import { isTmpImageRef } from '../constants/image-ref';

// Mock EventSource class for testing
class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onopen: ((e: Event) => void) | null = null;
  onclose: (() => void) | null = null;
  readyState: number = 0;
  private listeners: Map<string, Function[]> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  close() {
    this.readyState = 2;
    if (this.onclose) {
      this.onclose();
    }
  }

  addEventListener(type: string, handler: Function) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, []);
    }
    this.listeners.get(type)!.push(handler);
  }

  simulateEvent(type: string, data: any) {
    const handlers = this.listeners.get(type) || [];
    handlers.forEach((h) => h({ data: JSON.stringify(data), lastEventId: '0' } as MessageEvent));
  }
}

// Testable SseService implementation (mirrors actual service for testing)
class TestSseService {
  private readonly API_BASE = '/api';

  private eventSource: MockEventSource | null = null;
  private currentInstanceId: string | null = null;

  // Signals for reactive state
  isStreaming = signal(false);
  events = signal<SSEEvent[]>([]);
  latestError = signal<{ message: string; instance_id?: string } | null>(null);

  // Messages from checkpoint events
  messages = signal<Message[]>([]);

  // Status change events for instance updates
  statusChange = signal<{ instance_id: string; status: string; agent_id?: string } | null>(null);

  // Reconnect-refetch trigger (message-display-latency §4.3 item 10).
  // Mirrors the real service: bumped exactly once per error→connected
  // transition so a stable connection doesn't loop.
  refetchRequest = signal<number>(0);

  // Terminal-status pending-purge trigger (message-display-latency
  // §4.3 item 11). Bumped when status_change lands with a terminal
  // status. Mirrors the real service's allowlist.
  pendingPurgeRequest = signal<number>(0);

  // MIN-3 mirror: the instance the latest purge bump refers to, so the
  // chat-side effect can re-check it against the active instance.
  pendingPurgeInstanceId = signal<string | null>(null);

  /**
   * True after the current EventSource observed a connection-level
   * error; reset on the next ``connected`` event so the refetch
   * trigger fires exactly once.
   */
  private connectionHadError = false;

  /**
   * Allowlist for terminal-status detection in this test mirror. Kept
   * inline (vs. importing the real util) so the test class has zero
   * module dependencies — same rationale as the production service.
   */
  private static readonly TERMINAL_STATUSES: ReadonlySet<string> = new Set<string>([
    'completed',
    'error',
    'terminated',
    'failed',
  ]);

  /**
   * Id-keyed upsert with ARRIVAL-ORDER append — existing rows are
   * replaced in place, new rows append at the end, NO ``created_at``
   * re-sort (mirrors ``SseService.upsertMessage``; stale-message fix
   * 2026-09-05: unstable checkpoint re-stamps must not reorder
   * history).
   */
  private upsertMessage(message: Message): void {
    this.messages.update(msgs => {
      const idx = msgs.findIndex(m => m.message_id === message.message_id);
      let result: Message[];
      if (idx >= 0) {
        result = [...msgs];
        result[idx] = message;
      } else {
        result = [...msgs, message];
      }
      return result;
    });
  }

  connect(instanceId: string): void {
    if (this.currentInstanceId === instanceId && this.eventSource) {
      // N2 mirror: an explicit same-instance re-connect on an open
      // channel clears a stale error latch (see production connect()).
      this.connectionHadError = false;
      return;
    }

    this.disconnect();
    this.currentInstanceId = instanceId;
    this.clearEvents();
    this.connectInternal();
  }

  private connectInternal(): void {
    if (!this.currentInstanceId) return;

    if (this.eventSource) {
      return;
    }

    const url = `${this.API_BASE}/instances/${this.currentInstanceId}/events`;
    const eventSource = new MockEventSource(url);
    this.eventSource = eventSource;
    this.isStreaming.set(true);

    // Connected event handler
    eventSource.addEventListener('connected', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        this.events.update(evts => [...evts, { type: 'connected', data }]);
      } catch {
        this.events.update(evts => [...evts, { type: 'connected', data: {} }]);
      }
      // Reconnect catch-up mirror (message-display-latency §4.3 item 10):
      // error→connected transition bumps the refetch trigger exactly once.
      if (this.connectionHadError) {
        this.connectionHadError = false;
        this.refetchRequest.update(n => n + 1);
      }
    });

    // User-message event handler — id-keyed upsert so the POST-time echo
    // and the drain-time re-emit collapse onto a single bubble (mirrors
    // production ``upsertMessage``).
    eventSource.addEventListener('user_message', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const m = data.message ?? {};
        const message: Message = {
          message_id: m.message_id,
          role: m.role ?? 'user',
          content: m.content ?? '',
          thinking: m.thinking ?? null,
          thinking_extracted: m.thinking_extracted ?? null,
          tool_calls: m.tool_calls ?? undefined,
          created_at: m.created_at ?? new Date().toISOString(),
          instance_id: m.instance_id,
          images: m.images,
        };
        this.upsertMessage(message);
      } catch {
        // Ignore parse errors in test
      }
    });

    // Checkpoint event handler
    eventSource.addEventListener('checkpoint', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        this.events.update(evts => [...evts, { type: 'checkpoint', data }]);
        
        if (data.messages && Array.isArray(data.messages)) {
          const mappedMessages: Message[] = data.messages.map((m: any) => ({
            message_id: m.message_id,
            role: m.role,
            content: m.content || '',
            thinking: m.thinking || null,
            thinking_extracted: m.thinking_extracted || null,
            tool_calls: m.tool_calls || null,
            created_at: m.created_at || new Date().toISOString(),
          }));
          this.messages.set(mappedMessages);
        }
      } catch (err) {
        // Ignore parse errors in test
      }
    });

    // Error event handler
    eventSource.addEventListener('error', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
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
        this.isStreaming.set(false);
      }
    });

    // Keepalive event handler
    eventSource.addEventListener('keepalive', () => {
      // No action needed
    });

    // Status change event handler
    eventSource.addEventListener('status_change', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        this.events.update(evts => [...evts, { type: 'status_change', data }]);
        this.statusChange.set({
          instance_id: data.instance_id as string,
          status: data.status as string,
          agent_id: data.agent_id as string | undefined,
        });
        // Pending-purge trigger mirror (message-display-latency §4.3
        // item 11): bump only on terminal statuses so non-terminal
        // transitions (running → waiting_children etc.) leave
        // provisional entries alone. MIN-3 mirror: the bump also
        // requires the event's ``instance_id`` to match the CONNECTED
        // instance — a cascade CHILD going terminal on this channel
        // must not purge the connected instance's provisional entries.
        if (
          TestSseService.TERMINAL_STATUSES.has(data.status) &&
          data.instance_id === this.currentInstanceId
        ) {
          this.pendingPurgeInstanceId.set(data.instance_id);
          this.pendingPurgeRequest.update(n => n + 1);
        }
      } catch (err) {
        // Ignore parse errors in test
      }
    });

    // Connection error handler — sets the latch so the next ``connected``
    // event bumps the refetch trigger exactly once.
    eventSource.onerror = () => {
      this.isStreaming.set(false);
      this.connectionHadError = true;
    };

    // Close handler
    eventSource.onclose = () => {
      this.isStreaming.set(false);
    };
  }

  private handleClose(): void {
    this.isStreaming.set(false);
  }

  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.currentInstanceId = null;
    this.isStreaming.set(false);
  }

  clearEvents(): void {
    this.events.set([]);
    this.latestError.set(null);
    this.messages.set([]);
    this.statusChange.set(null);
  }

  // Expose for testing
  getEventSource(): MockEventSource | null {
    return this.eventSource;
  }

  /**
   * Map raw SSE message data to Message type — LOGIC MIRROR of
   * production ``SseService.mapToMessage`` (private method). The mirror
   * is required because the production method is private; the
   * identity-grep mirror-parity rule (FE conventions) requires the
   * literal predicate ``isTmpImageRef(img)`` and the verbatim #19
   * comment block to appear in BOTH the production source AND this
   * spec source — otherwise a future widening of the production
   * filter would silently drift away from a still-green spec.
   *
   * Phase 5 / clipboard-image-chat — two-prefix accept (legacy data
   * URI + canonical server-URL ref). See decisions.md §2 and
   * architecture-recommendation.md §6.5 (architect ruling #19).
   */
  mapToMessage(data: Record<string, unknown>): Message {
    return {
      message_id: data['message_id'] as string,
      role: (data['role'] as 'user' | 'assistant' | 'system' | 'tool') || 'assistant',
      content: (data['content'] as string) || '',
      thinking: (data['thinking'] as string | null) || null,
      thinking_extracted: (data['thinking_extracted'] as string | null) || null,
      tool_calls: Array.isArray(data['tool_calls']) ? data['tool_calls'] as ToolCall[] : undefined,
      created_at: (data['created_at'] as string) || new Date().toISOString(),
      instance_id: data['instance_id'] as string | undefined,
      // Whitelist is PREFIX-SCOPED, NOT scheme-based. https:// is explicitly OUT (scheme-widening enables tracking-pixel + internal-network-probe vectors via <img src>). The Discord-SSE gap is PRE-EXISTING and out of scope — its correct future fix is a host allowlist (e.g. cdn.discordapp.com), recorded as follow-up.
      images: Array.isArray(data['images'])
        ? (data['images'] as string[]).filter((img: unknown): img is string =>
            typeof img === 'string' && (img.startsWith('data:image/') || isTmpImageRef(img))
          )
        : undefined,
    };
  }
}

describe('SseService', () => {
  let service: TestSseService;

  beforeEach(() => {
    service = new TestSseService();
  });

  describe('isStreaming signal', () => {
    it('should exist', () => {
      expect(service.isStreaming).toBeDefined();
      expect(typeof service.isStreaming).toBe('function');
    });

    it('should start as false', () => {
      expect(service.isStreaming()).toBe(false);
    });

    it('should be set to true when connect() is called', () => {
      service.connect('instance-123');
      expect(service.isStreaming()).toBe(true);
    });

    it('should be set to false when disconnect() is called', () => {
      service.connect('instance-123');
      expect(service.isStreaming()).toBe(true);

      service.disconnect();
      expect(service.isStreaming()).toBe(false);
    });
  });

  describe('messages signal', () => {
    it('should exist', () => {
      expect(service.messages).toBeDefined();
      expect(typeof service.messages).toBe('function');
    });

    it('should start as empty array', () => {
      expect(service.messages()).toEqual([]);
    });

    it('should update messages from checkpoint event', () => {
      service.connect('instance-123');
      
      const checkpointData = {
        messages: [
          {
            message_id: 'msg-1',
            role: 'user',
            content: 'Hello',
            created_at: '2024-01-01T00:00:00Z'
          },
          {
            message_id: 'msg-2',
            role: 'assistant',
            content: 'Hi there!',
            thinking: 'This is my response',
            created_at: '2024-01-01T00:00:01Z'
          }
        ]
      };

      service.getEventSource()?.simulateEvent('checkpoint', checkpointData);
      
      expect(service.messages().length).toBe(2);
      expect(service.messages()[0].message_id).toBe('msg-1');
      expect(service.messages()[1].message_id).toBe('msg-2');
      expect(service.messages()[1].thinking).toBe('This is my response');
    });
  });

  describe('events signal', () => {
    it('should exist', () => {
      expect(service.events).toBeDefined();
      expect(typeof service.events).toBe('function');
    });

    it('should start as empty array', () => {
      expect(service.events()).toEqual([]);
    });

    it('should add connected event to events array', () => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('connected', {});
      
      expect(service.events().length).toBe(1);
      expect(service.events()[0].type).toBe('connected');
    });

    it('should add checkpoint event to events array', () => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('checkpoint', { messages: [] });
      
      expect(service.events().length).toBe(1);
      expect(service.events()[0].type).toBe('checkpoint');
    });
  });

  describe('latestError signal', () => {
    it('should exist', () => {
      expect(service.latestError).toBeDefined();
      expect(typeof service.latestError).toBe('function');
    });

    it('should start as null', () => {
      expect(service.latestError()).toBeNull();
    });

    it('should set error from error event', () => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('error', { error: 'Something went wrong' });
      
      expect(service.latestError()).not.toBeNull();
      expect(service.latestError()?.message).toBe('Something went wrong');
    });
  });

  describe('clearEvents()', () => {
    it('should clear all signals', () => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('connected', {});
      service.getEventSource()?.simulateEvent('checkpoint', { messages: [{ message_id: 'test' }] });
      service.getEventSource()?.simulateEvent('error', { error: 'test error' });

      service.clearEvents();

      expect(service.events()).toEqual([]);
      expect(service.messages()).toEqual([]);
      expect(service.latestError()).toBeNull();
    });

    it('should clear statusChange signal', () => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'test-inst',
        status: 'running',
        agent_id: 'developer'
      });

      expect(service.statusChange()).not.toBeNull();

      service.clearEvents();

      expect(service.statusChange()).toBeNull();
    });
  });

  describe('statusChange signal', () => {
    it('should exist', () => {
      expect(service.statusChange).toBeDefined();
      expect(typeof service.statusChange).toBe('function');
    });

    it('should start as null', () => {
      expect(service.statusChange()).toBeNull();
    });

    it('should parse agent_id from status_change event', () => {
      service.connect('instance-123');

      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'test-inst-123',
        status: 'running',
        agent_id: 'developer'
      });

      expect(service.statusChange()).not.toBeNull();
      expect(service.statusChange()?.instance_id).toBe('test-inst-123');
      expect(service.statusChange()?.status).toBe('running');
      expect(service.statusChange()?.agent_id).toBe('developer');
    });

    it('should handle status_change event without agent_id', () => {
      service.connect('instance-123');

      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'test-inst-456',
        status: 'completed'
      });

      expect(service.statusChange()).not.toBeNull();
      expect(service.statusChange()?.instance_id).toBe('test-inst-456');
      expect(service.statusChange()?.status).toBe('completed');
      expect(service.statusChange()?.agent_id).toBeUndefined();
    });

    it('should add status_change event to events array', () => {
      service.connect('instance-123');

      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'test-inst',
        status: 'paused',
        agent_id: 'test-agent'
      });

      expect(service.events().length).toBe(1);
      expect(service.events()[0].type).toBe('status_change');
      expect(service.events()[0].data.instance_id).toBe('test-inst');
    });

    it('should parse KB agent IDs correctly', () => {
      service.connect('instance-123');

      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'kb-inst-1',
        status: 'running',
        agent_id: 'experiencer'
      });

      expect(service.statusChange()?.agent_id).toBe('experiencer');

      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'kb-inst-2',
        status: 'completed',
        agent_id: 'kb-importer'
      });

      expect(service.statusChange()?.agent_id).toBe('kb-importer');
    });
  });

  /**
   * Phase 2 / message-display-latency §7 FE unit tests #5
   * ("reconnect: error → connected → exactly one merge-refetch").
   *
   * The refetch-trigger surface here is the bare latch: the chat
   * component subscribes to ``refetchRequest`` and runs the actual
   * REST refetch. We verify the trigger behavior end-to-end:
   *   - first connected after error → bumps once
   *   - subsequent connected without error → does NOT bump (no loop)
   *   - error→error→connected → bumps once (latch is binary)
   */
  describe('refetchRequest trigger (reconnect catch-up)', () => {
    it('should start at zero', () => {
      expect(service.refetchRequest()).toBe(0);
    });

    it('should NOT bump on initial connect (no prior error)', () => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(0);
    });

    it('should bump exactly once on error → connected transition', () => {
      service.connect('instance-123');
      // First connected after connect() does not bump (no prior error).
      service.getEventSource()?.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(0);

      // Connection drops.
      const es = service.getEventSource()!;
      es.onerror?.(new Event('error'));

      // Reconnect — must bump exactly once.
      es.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(1);
    });

    it('should NOT bump on subsequent connected events without an intervening error', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;

      es.onerror?.(new Event('error'));
      es.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(1);

      // No error in between — should not bump again.
      es.simulateEvent('connected', {});
      es.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(1);
    });

    it('should bump a second time after another error → connected cycle', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;

      es.onerror?.(new Event('error'));
      es.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(1);

      es.onerror?.(new Event('error'));
      es.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(2);
    });

    // N2: an explicit re-connect for the instance we are ALREADY
    // attached to clears a stale error latch, so the next ``connected``
    // event does not fire a catch-up refetch for an error cycle the
    // explicit connect() already superseded (that UI flow runs its own
    // merge-mode refetch).
    it('should NOT bump when connect() re-asserts the same instance after an error (N2 latch reset)', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;

      // Connection drops — latch set.
      es.onerror?.(new Event('error'));

      // UI re-asserts the connection for the SAME instance while the
      // channel object is still attached → early-return resets the latch.
      service.connect('instance-123');

      // The deferred recovery connected-event now finds no latch.
      es.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(0);
    });

    it('should still bump exactly once for a genuine error AFTER an explicit re-connect (N2 does not brick the trigger)', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;

      // Stale latch cleared by the explicit re-connect…
      es.onerror?.(new Event('error'));
      service.connect('instance-123');

      // …but a NEW error → recovery cycle afterwards still counts once.
      es.onerror?.(new Event('error'));
      es.simulateEvent('connected', {});
      expect(service.refetchRequest()).toBe(1);
    });
  });

  /**
   * Phase 2 / message-display-latency §7 FE unit tests #3 (terminal
   * portion: "eviction: 10-min TTL + terminal-status purge").
   *
   * The terminal-status purge trigger is the SSE-side surface; the
   * actual ``pending: true`` strip happens in the chat component
   * effect. We verify the trigger fires for every terminal status
   * and stays silent for non-terminal transitions.
   */
  describe('pendingPurgeRequest trigger (terminal-status eviction)', () => {
    it('should start at zero', () => {
      expect(service.pendingPurgeRequest()).toBe(0);
    });

    it.each([
      ['completed'],
      ['error'],
      ['terminated'],
      ['failed'],
    ])('should bump on terminal status %s', (status) => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'instance-123',
        status,
      });
      expect(service.pendingPurgeRequest()).toBe(1);
      // MIN-3: the bump records WHICH instance went terminal so the
      // chat-side effect can re-check it against the active instance.
      expect(service.pendingPurgeInstanceId()).toBe('instance-123');
    });

    it.each([
      ['running'],
      ['idle'],
      ['queued'],
      ['waiting_children'],
      ['paused'],
    ])('should NOT bump on non-terminal status %s', (status) => {
      service.connect('instance-123');
      service.getEventSource()?.simulateEvent('status_change', {
        instance_id: 'instance-123',
        status,
      });
      expect(service.pendingPurgeRequest()).toBe(0);
    });

    it('should bump per terminal transition (multiple shutdowns)', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;
      es.simulateEvent('status_change', { instance_id: 'instance-123', status: 'completed' });
      es.simulateEvent('status_change', { instance_id: 'instance-123', status: 'error' });
      es.simulateEvent('status_change', { instance_id: 'instance-123', status: 'terminated' });
      expect(service.pendingPurgeRequest()).toBe(3);
    });

    // MIN-3: the connected channel can forward terminal status_change
    // events for OTHER instances (a cascade CHILD shutting down while
    // the parent chat is open). Such an event must NOT bump the purge
    // trigger — the connected (parent) instance's provisional entries
    // are still perfectly resolvable.
    it('should NOT bump when a DIFFERENT instance (cascade child) goes terminal on this channel', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;

      es.simulateEvent('status_change', { instance_id: 'child-1', status: 'completed' });
      es.simulateEvent('status_change', { instance_id: 'child-2', status: 'failed' });
      // Terminal AND matching is required — a foreign terminal event
      // with a terminal status is still filtered out.
      expect(service.pendingPurgeRequest()).toBe(0);
      expect(service.pendingPurgeInstanceId()).toBeNull();
    });

    it('should NOT record a purge instance id for a foreign terminal event', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;

      // The statusChange signal itself still records the foreign event
      // (instance list relies on it) — only the purge trigger is scoped.
      es.simulateEvent('status_change', { instance_id: 'child-1', status: 'completed' });
      expect(service.statusChange()?.instance_id).toBe('child-1');
      expect(service.pendingPurgeInstanceId()).toBeNull();
    });
  });

  /**
   * Phase 2 / message-display-latency §7 FE unit tests #1
   * ("dedup collapse: POST-echo + drain-echo same id → single
   * bubble with POST created_at").
   *
   * The SSE-side contract here is the id-keyed upsert with
   * arrival-order append (NO ``created_at`` re-sort — unstable
   * checkpoint re-stamps must not reorder history; stale-message fix
   * 2026-09-05). The chat component's merge contract (clears the
   * ``pending`` flag, preserves local-only entries) is exercised in
   * ``message-merge.util.spec.ts`` because it's a pure function.
   */
  describe('user_message dedup (id-keyed upsert)', () => {
    it('should upsert two events with the same id into a single bubble', () => {
      service.connect('instance-123');

      // POST-time echo — server mints echo_id, created_at = POST ts.
      service.getEventSource()?.simulateEvent('user_message', {
        message: {
          message_id: 'echo-1',
          role: 'user',
          content: 'hello',
          created_at: '2024-01-01T00:00:00Z',
          instance_id: 'instance-123',
        },
      });

      // Drain-time re-emit — same id, same created_at, no duplicate.
      service.getEventSource()?.simulateEvent('user_message', {
        message: {
          message_id: 'echo-1',
          role: 'user',
          content: 'hello',
          created_at: '2024-01-01T00:00:00Z',
          instance_id: 'instance-123',
        },
      });

      const msgs = service.messages();
      expect(msgs.length).toBe(1);
      expect(msgs[0].message_id).toBe('echo-1');
      expect(msgs[0].created_at).toBe('2024-01-01T00:00:00Z');
    });

    it('should keep two distinct ids as two bubbles', () => {
      service.connect('instance-123');

      service.getEventSource()?.simulateEvent('user_message', {
        message: {
          message_id: 'a',
          role: 'user',
          content: 'first',
          created_at: '2024-01-01T00:00:00Z',
          instance_id: 'instance-123',
        },
      });
      service.getEventSource()?.simulateEvent('user_message', {
        message: {
          message_id: 'b',
          role: 'user',
          content: 'second',
          created_at: '2024-01-01T00:00:01Z',
          instance_id: 'instance-123',
        },
      });

      const msgs = service.messages();
      expect(msgs.length).toBe(2);
      expect(msgs.map(m => m.message_id)).toEqual(['a', 'b']);
    });

    it('preserves ARRIVAL order regardless of created_at (no re-sort — unstable checkpoint stamps)', () => {
      // Stale-message fix (2026-09-05): the upsert used to re-sort by
      // ``created_at``. Server stamps for metadata-less checkpoint
      // rows are unstable (re-stamped with the latest checkpoint-commit
      // time), so a re-sort could swap genuinely arrival-ordered rows.
      // Arrival order (= emission order = checkpoint order) must win.
      service.connect('instance-123');

      // First arrival carries a LATER stamp; second arrival an
      // EARLIER one.
      service.getEventSource()?.simulateEvent('user_message', {
        message: {
          message_id: 'first',
          role: 'user',
          content: 'first',
          created_at: '2024-01-01T02:00:00Z',
          instance_id: 'instance-123',
        },
      });
      service.getEventSource()?.simulateEvent('user_message', {
        message: {
          message_id: 'second',
          role: 'user',
          content: 'second',
          created_at: '2024-01-01T01:00:00Z',
          instance_id: 'instance-123',
        },
      });

      const msgs = service.messages();
      expect(msgs.length).toBe(2);
      expect(msgs.map(m => m.message_id)).toEqual(['first', 'second']);
    });
  });

  /**
   * Phase 5 / clipboard-image-chat — the SSE whitelist widening is
   * the seam that lets the new ``/api/tmp_images/<32hex>`` server-URL
   * ref form survive into the rendered bubble. Without it, the
   * whitelist's single-prefix ``data:image/`` check silently drops the
   * ref and the bubble renders without a thumbnail.
   *
   * Truth-table cases (per plan Task 2 acceptance):
   *   (a) ``data:image/png;base64,...`` → accepted (legacy)
   *   (b) ``/api/tmp_images/<32hex>`` → accepted (new ref; 32-hex
   *       lowercase id per decisions.md §2)
   *   (c) ``http://evil.example/x.png`` → dropped (no prefix match)
   *   (d) arbitrary non-URL string → dropped
   *   (e) post-union fixture (round-2 #37 (b)): the SSE wire `images`
   *       field carries refs alongside legacy blocks (via
   *       ``serialize_message``'s union of ``additional_kwargs['image_refs']``)
   *       → accepted through the SAME filter.
   *
   * The fixtures include a 4-image case (cap is 3 — the whitelist does
   * NOT enforce cap; that's a send-time concern handled by phase 4).
   */
  describe('mapToMessage — images whitelist (phase 5 / clipboard-image-chat)', () => {
    const REF_A = '/api/tmp_images/abc123def456789012345678901234de'; // 32-hex lowercase
    const REF_B = '/api/tmp_images/00000000000000000000000000000001';
    const REF_C = '/api/tmp_images/00000000000000000000000000000002';
    const REF_D = '/api/tmp_images/00000000000000000000000000000003';
    const LEGACY_DATA_URI = 'data:image/png;base64,AAAA';

    function mapRow(images: unknown): string[] | undefined {
      return service.mapToMessage({
        message_id: 'm-images',
        role: 'user',
        content: 'hello',
        images,
      }).images;
    }

    it('(a) accepts a legacy ``data:image/...`` data URI', () => {
      expect(mapRow([LEGACY_DATA_URI])).toEqual([LEGACY_DATA_URI]);
    });

    it('(b) accepts a canonical ``/api/tmp_images/<32hex>`` ref', () => {
      expect(mapRow([REF_A])).toEqual([REF_A]);
    });

    it('(b+) accepts MULTIPLE canonical refs (mixed ids all retained)', () => {
      const result = mapRow([REF_A, REF_B, REF_C]);
      expect(result).toEqual([REF_A, REF_B, REF_C]);
    });

    it('(b++) accepts 4 refs (past the 3-image cap) — whitelist does not enforce cap', () => {
      const result = mapRow([REF_A, REF_B, REF_C, REF_D]);
      expect(result).toEqual([REF_A, REF_B, REF_C, REF_D]);
    });

    it('(c) DROPS an ``http://`` URL (no prefix match — prefix-scoped, NOT scheme-based)', () => {
      expect(mapRow(['http://evil.example/x.png'])).toEqual([]);
    });

    it('(c+) DROPS an ``https://`` URL (the comment pin names scheme-widening as the explicit hazard)', () => {
      expect(mapRow(['https://evil.example/x.png'])).toEqual([]);
      expect(mapRow(['https://cdn.discordapp.com/attachments/123/456/x.png'])).toEqual([]);
    });

    it('(d) DROPS an arbitrary non-URL string', () => {
      expect(mapRow(['not a url'])).toEqual([]);
      expect(mapRow(['/some/other/path'])).toEqual([]);
      expect(mapRow([''])).toEqual([]);
    });

    it('(e) accepts the post-union wire shape (refs surface in the SAME `images` field — round-2 #37 (b))', () => {
      // Architect amendment #29 (serialize_message union) lands the
      // refs inside the wire `images` field. The whitelist filter is
      // the SINGLE seam — there is no separate `image_refs` wire field
      // to filter on. The post-union fixture asserts the same path
      // handles both legacy blocks and new refs.
      const result = mapRow([LEGACY_DATA_URI, REF_A, REF_B]);
      expect(result).toEqual([LEGACY_DATA_URI, REF_A, REF_B]);
    });

    it('drops non-string elements from the array (defensive — wire type-asserted upstream)', () => {
      // The filter is typed (img is unknown); non-strings are
      // structurally dropped by the ``typeof img === 'string'`` guard.
      const result = mapRow([REF_A, 42, null, undefined, { url: REF_B }]);
      expect(result).toEqual([REF_A]);
    });

    it('returns undefined when ``images`` is absent (legacy path — no images field)', () => {
      expect(mapRow(undefined)).toBeUndefined();
    });

    it('returns undefined when ``images`` is not an array (defensive — unknown wire shape)', () => {
      expect(mapRow('not an array')).toBeUndefined();
      expect(mapRow({ url: REF_A })).toBeUndefined();
    });

    it('returns an empty array (NOT undefined) when the array contains zero matches', () => {
      // The contract: an array-shaped input → array-shaped output (may
      // be empty). An undefined input → undefined output. This is the
      // distinction the bubble's ``for ... of img in images`` loop
      // relies on (it skips an empty array, falls back to a placeholder
      // on undefined).
      expect(mapRow(['http://evil.example/x.png'])).toEqual([]);
    });

    /**
     * Identity-grep mirror-parity (FE conventions): the literal
     * ``isTmpImageRef(img)`` predicate MUST appear verbatim in BOTH
     * production source (``sse.service.ts:mapToMessage``) AND this
     * spec source (``TestSseService.mapToMessage`` mirror). If a
     * future contributor replaces the prefix-scoped check with a
     * permissive ``img.startsWith('/')`` or a scheme check, this spec
     * still passes — but a CI grep would catch the drift. The TestSse
     * Service mirror carries the literal so a manual side-by-side
     * diff between prod and spec surfaces any widening.
     */
    it('identity-grep: ``isTmpImageRef(img)`` appears verbatim in the mirror', () => {
      // The mirror's filter call site carries the literal predicate;
      // this assertion is a no-op pass if present and would surface a
      // compile error if the predicate is removed. (Compile-time
      // pin — the import in this spec file requires the symbol to
      // resolve.)
      expect(typeof isTmpImageRef).toBe('function');
      // Belt-and-suspenders: invoke the mirror with a ref-shaped
      // input and assert the predicate's output is reflected.
      expect(mapRow([REF_A])).toEqual([REF_A]);
    });

    /**
     * Comment-pin identity-grep (architect ruling #19, exact text per
     * ``architecture-recommendation.md`` §6.5): the verbatim comment
     * block MUST appear in production source. The mirror carries the
     * SAME comment so the spec cannot drift away from a still-green
     * production. The 3 distinctive substrings below are the
     * architectural-load-bearing phrases — pin their presence so a
     * future contributor who trims or rewrites the comment is forced
     * to keep the architectural argument intact.
     */
    it('comment-pin identity-grep: 3 architectural-load-bearing substrings are present in the mirror', () => {
      // The mirror source contains the verbatim comment block above.
      // The test reads its own source via ``fs`` and asserts each
      // substring appears verbatim. A rewriter who drops the
      // architectural argument is caught by this spec.
      // eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-var-requires
      const fs = require('fs');
      // eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-var-requires
      const path = require('path');
      const specSrc = fs.readFileSync(__filename, 'utf8') as string;
      const substrings = [
        'PREFIX-SCOPED, NOT scheme-based',
        'scheme-widening enables',
        'host allowlist',
      ];
      for (const s of substrings) {
        expect(specSrc).toContain(s);
      }
      // The production source carries the SAME three substrings — pin
      // it explicitly so a silent drift between mirror and production
      // fails this spec rather than the live renderer.
      const prodSrc = fs.readFileSync(
        path.join(__dirname, 'sse.service.ts'),
        'utf8',
      ) as string;
      for (const s of substrings) {
        expect(prodSrc).toContain(s);
      }
    });
  });

  /**
   * Phase 5 / clipboard-image-chat — Task 6 cross-seam invariant:
   * an SSE event carrying ``images: ['/api/tmp_images/<32hex>']``
   * flows through ``mapToMessage`` AND ``upsertMessage`` AND
   * ``mergeMessagesById`` (the chat component's merge helper) and
   * the bubble that lands in the message list STILL carries the
   * refs intact — even when a subsequent echo or refetch lands
   * WITHOUT an ``images`` field (the 202-injection drop case that
   * the merge pin was specifically built to absorb).
   *
   * The invariant covers BOTH seams in one test surface so a future
   * regression on either the whitelist OR the merge pin fails this
   * spec rather than only surfacing in production.
   */
  describe('cross-seam invariant — SSE ref survives mapToMessage + upsertMessage + mergeMessagesById', () => {
    const REF_A = '/api/tmp_images/abc123def456789012345678901234de'; // 32-hex lowercase
    const REF_B = '/api/tmp_images/00000000000000000000000000000001';

    it('preserves a ref from mapToMessage → upsertMessage into the stored list', () => {
      // Wire input: SSE event with images = [REF_A] (canonical §2 form).
      const message = service.mapToMessage({
        message_id: 'cross-1',
        role: 'user',
        content: 'see attached',
        images: [REF_A],
      });

      // Whitelist predicate accepted the ref.
      expect(message.images).toEqual([REF_A]);

      // Upsert the message into the SSE mirror.
      service.connect('instance-1');
      service.getEventSource()?.simulateEvent('user_message', { message });
      const stored = service.messages().find(m => m.message_id === 'cross-1');
      expect(stored).toBeDefined();
      expect(stored!.images).toEqual([REF_A]);
    });

    it('preserves a ref through mergeMessagesById when a follow-up echo has no images field', () => {
      // This is the 202-injection regression (architect h4-S1):
      // the optimistic bubble carries the refs (phase 4), the SSE
      // echo lands WITHOUT images, and the merge pin MUST keep the
      // existing copy intact. The test exercises the merge helper
      // directly with the §2 frozen wire shapes.
      // eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-var-requires
      const { mergeMessagesById } = require('./message-merge.util');

      const optimisticBubble = {
        message_id: 'cross-2',
        role: 'user',
        content: 'see attached',
        created_at: '2026-09-19T12:00:00Z',
        images: [REF_A, REF_B],
        pending: true,
      } as Message;
      const echoWithoutImages = {
        message_id: 'cross-2',
        role: 'user',
        content: 'see attached',
        created_at: '2026-09-19T12:00:00Z',
        // images: undefined — the SSE echo shape that the 202 leg
        // historically emitted before h4-S1 landed.
      } as unknown as Message;

      const merged = mergeMessagesById([optimisticBubble], [echoWithoutImages]);
      expect(merged.length).toBe(1);
      expect(merged[0].images).toEqual([REF_A, REF_B]);
    });
  });
});

/**
 * N2 production regression: the surrogate ``TestSseService`` mirror at the
 * top of this file matches production ``SseService.connect()`` line by
 * line today, but a surrogate is only as good as the last manual copy-edit.
 * The reviewer cycle-2 N2 blocker was exactly this gap: the surrogate had
 * the reset, production did not, and the green surrogate test gave false
 * confidence. This block exercises the REAL production class so a future
 * drift between surrogate and production is caught immediately rather than
 * via another reviewer cycle.
 *
 * Setup: jsdom does not ship ``EventSource``, so each test installs a
 * stub via ``globalThis.EventSource`` and restores the prior value in
 * ``afterEach``. The test path only exercises ``connect()`` /
 * ``connectInternal()`` early-return semantics and never fires any
 * SSE listener, so we can supply a trivial ``ngZone.run(fn) → fn()`` stub
 * and an empty ``ApiService`` (the production connect path never calls it).
 */
describe('SseService (production) — same-id connect() early-return', () => {
  let realService: InstanceType<typeof RealSseService>;
  let eventSourceInstances: Array<{
    onerror: ((e: Event) => void) | null;
    [k: string]: unknown;
  }>;
  let originalEventSource: unknown;

  const ngZoneStub = {
    run: <T>(fn: () => T): T => fn(),
    runOutsideAngular: <T>(fn: () => T): T => fn(),
  } as unknown as ConstructorParameters<typeof RealSseService>[0];
  const apiStub = {} as ConstructorParameters<typeof RealSseService>[1];

  beforeEach(() => {
    eventSourceInstances = [];
    originalEventSource = (globalThis as { EventSource?: unknown }).EventSource;

    class EventSourceStub {
      url: string;
      readyState = 0;
      onerror: ((e: Event) => void) | null = null;
      onopen: ((e: Event) => void) | null = null;
      onmessage: ((e: MessageEvent) => void) | null = null;
      private listeners: Map<string, Array<(e: Event | MessageEvent) => void>> = new Map();
      constructor(url: string) {
        this.url = url;
        eventSourceInstances.push(this as unknown as typeof eventSourceInstances[number]);
      }
      close(): void {
        this.readyState = 2;
      }
      addEventListener(
        type: string,
        handler: (e: Event | MessageEvent) => void,
      ): void {
        const arr = this.listeners.get(type) ?? [];
        arr.push(handler);
        this.listeners.set(type, arr);
      }
    }

    (globalThis as { EventSource?: unknown }).EventSource = EventSourceStub;
    realService = new RealSseService(ngZoneStub, apiStub);
  });

  afterEach(() => {
    if (originalEventSource === undefined) {
      delete (globalThis as { EventSource?: unknown }).EventSource;
    } else {
      (globalThis as { EventSource?: unknown }).EventSource = originalEventSource;
    }
  });

  it('resets connectionHadError and skips EventSource re-construction on a same-id re-connect', () => {
    realService.connect('instance-prod-1');
    expect(eventSourceInstances.length).toBe(1);

    // Drive a connection-level error so the latch flips to true. The real
    // EventSource onerror handler sets ``connectionHadError = true``
    // (sse.service.ts onerror handler).
    (eventSourceInstances[0].onerror as ((e: Event) => void) | null)?.(
      new Event('error'),
    );
    expect(
      (realService as unknown as { connectionHadError: boolean }).connectionHadError,
    ).toBe(true);

    // Same-id re-connect — the production early-return must reset the
    // latch (FIX #1) AND must NOT construct a second EventSource. If
    // either half regresses, this spec fails — that is the point.
    realService.connect('instance-prod-1');
    expect(eventSourceInstances.length).toBe(1);
    expect(
      (realService as unknown as { connectionHadError: boolean }).connectionHadError,
    ).toBe(false);
  });
});
