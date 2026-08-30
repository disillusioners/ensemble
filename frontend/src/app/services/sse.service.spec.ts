import { signal } from '@angular/core';
import type { Message, SSEEvent } from '../models';

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
   * Id-keyed upsert with sort by ``created_at`` — the canonical merge
   * behavior the SSE mirror effect relies on (mirrors
   * ``SseService.upsertMessage``).
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
      result.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
      return result;
    });
  }

  connect(instanceId: string): void {
    if (this.currentInstanceId === instanceId && this.eventSource) {
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
        // provisional entries alone.
        if (TestSseService.TERMINAL_STATUSES.has(data.status)) {
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
        instance_id: 'inst',
        status,
      });
      expect(service.pendingPurgeRequest()).toBe(1);
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
        instance_id: 'inst',
        status,
      });
      expect(service.pendingPurgeRequest()).toBe(0);
    });

    it('should bump per terminal transition (multiple shutdowns)', () => {
      service.connect('instance-123');
      const es = service.getEventSource()!;
      es.simulateEvent('status_change', { instance_id: 'inst', status: 'completed' });
      es.simulateEvent('status_change', { instance_id: 'inst', status: 'error' });
      es.simulateEvent('status_change', { instance_id: 'inst', status: 'terminated' });
      expect(service.pendingPurgeRequest()).toBe(3);
    });
  });

  /**
   * Phase 2 / message-display-latency §7 FE unit tests #1
   * ("dedup collapse: POST-echo + drain-echo same id → single
   * bubble with POST created_at").
   *
   * The SSE-side contract here is the id-keyed upsert with sort by
   * ``created_at``. The chat component's merge contract (clears the
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
  });
});
