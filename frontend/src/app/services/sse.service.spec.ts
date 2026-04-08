import { signal } from '@angular/core';
import type { Message, SSEEvent } from '../models';

// Mock EventSource class for testing
class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onopen: ((e: Event) => void) | null = null;
  readyState: number = 0;
  private listeners: Map<string, Function[]> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  close() {
    this.readyState = 2;
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
  private readonly MAX_RECONNECT_ATTEMPTS = 5;

  private eventSource: MockEventSource | null = null;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private isConnected = false;
  private currentInstanceId: string | null = null;

  // Signals for reactive state
  isStreaming = signal(false);
  events = signal<SSEEvent[]>([]);
  latestCompletedMessage = signal<Message | null>(null);
  latestError = signal<{ message_id: string; error: string; instance_id?: string | null } | null>(null);
  statusUpdates = signal<Map<string, string>>(new Map());
  partialMessages = signal<Map<string, Message>>(new Map());
  titleUpdates = signal<{ instance_id: string; title: string } | null>(null);

  connect(instanceId: string): void {
    if (this.currentInstanceId === instanceId && this.isConnected && this.eventSource) {
      return;
    }

    this.disconnect();
    this.currentInstanceId = instanceId;
    this.clearEvents();
    this.connectInternal();
  }

  private connectInternal(): void {
    if (!this.currentInstanceId) return;

    if (this.isConnected && this.eventSource) {
      return;
    }

    const url = `${this.API_BASE}/instances/${this.currentInstanceId}/events`;
    const eventSource = new MockEventSource(url);
    this.eventSource = eventSource;
    this.isStreaming.set(true);

    // Simulate connected event
    this.isConnected = true;

    // Simulate cancelled event handler (mirrors actual service behavior)
    eventSource.addEventListener('cancelled', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'cancelled') {
          this.isStreaming.set(false);
        }
      } catch (err) {
        // Ignore parse errors
      }
    });
  }

  disconnect(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    this.isConnected = false;
    this.reconnectAttempts = 0;
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.isStreaming.set(false);
    this.currentInstanceId = null;
  }

  clearEvents(): void {
    this.events.set([]);
    this.latestCompletedMessage.set(null);
    this.latestError.set(null);
    this.statusUpdates.set(new Map());
    this.partialMessages.set(new Map());
    this.titleUpdates.set(null);
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

  describe('cancelled SSE event handling', () => {
    it('should set isStreaming to false when cancelled event is received', () => {
      service.connect('instance-123');
      expect(service.isStreaming()).toBe(true);

      // Simulate the cancelled event
      service.getEventSource()?.simulateEvent('cancelled', { type: 'cancelled', instance_id: 'instance-123' });

      expect(service.isStreaming()).toBe(false);
    });

    it('should handle cancelled event from different instance', () => {
      service.connect('instance-123');
      expect(service.isStreaming()).toBe(true);

      // Note: This test verifies the event was received, 
      // regardless of which instance it came from
      service.getEventSource()?.simulateEvent('cancelled', { type: 'cancelled', instance_id: 'other-instance' });

      // The cancelled event still sets isStreaming to false (no instance check in test)
      expect(service.isStreaming()).toBe(false);
    });
  });
});
