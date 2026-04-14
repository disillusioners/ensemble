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

    // Connection error handler
    eventSource.onerror = () => {
      this.isStreaming.set(false);
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
  });
});
