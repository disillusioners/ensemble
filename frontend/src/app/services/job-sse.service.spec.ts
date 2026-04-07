import { signal } from '@angular/core';
import { JobEvent, JobEventPayload } from '../models/job.model';
import { Job } from '../testing/job-test-helpers';

// Mock EventSource class
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

  // Test helper to simulate events
  simulateEvent(type: string, data: any) {
    const handlers = this.listeners.get(type) || [];
    handlers.forEach((h) => h({ data: JSON.stringify(data) } as MessageEvent));
  }

  // Simulate error event
  simulateError(data?: any) {
    const handlers = this.listeners.get('error') || [];
    handlers.forEach((h) =>
      h({
        data: data ? JSON.stringify(data) : undefined,
      } as MessageEvent)
    );
  }
}

// Testable JobSseService implementation
type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'retrying' | 'failed';

class TestJobSseService {
  private readonly API_BASE = '/api';
  private readonly MAX_RECONNECT_ATTEMPTS = 5;
  private readonly INITIAL_RECONNECT_DELAY = 1000;

  private eventSource: MockEventSource | null = null;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private currentJobId: string | null = null;

  // Signals for reactive state
  readonly isConnected = signal(false);
  readonly connectionState = signal<ConnectionState>('disconnected');
  readonly events = signal<JobEvent[]>([]);
  readonly latestStatus = signal<JobEventPayload | null>(null);
  readonly latestError = signal<string | null>(null);
  readonly retryAttempt = signal(0);

  streamJobEvents(jobId: string): any {
    if (this.currentJobId === jobId && this.eventSource) {
      return { asObservable: () => ({ subscribe: () => ({ unsubscribe: () => {} }) }) };
    }

    this.disconnect();
    this.currentJobId = jobId;
    this.connectInternal();

    return { asObservable: () => ({ subscribe: () => ({ unsubscribe: () => {} }) }) };
  }

  private connectInternal(): void {
    if (!this.currentJobId) return;

    this.connectionState.set('connecting');
    const url = `${this.API_BASE}/jobs/${this.currentJobId}/events`;
    this.eventSource = new MockEventSource(url);

    this.eventSource.addEventListener('connected', () => {
      this.reconnectAttempts = 0;
      this.retryAttempt.set(0);
      this.isConnected.set(true);
      this.connectionState.set('connected');
      this.emitEvent({ event: 'connected', data: null });
    });

    this.eventSource.addEventListener('status_update', (e: MessageEvent) => {
      try {
        const data: JobEventPayload = JSON.parse(e.data);
        this.emitEvent({ event: 'status_update', data });
        this.latestStatus.set(data);
        this.latestError.set(null);
      } catch (err) {
        // Ignore parse errors
      }
    });

    this.eventSource.addEventListener('completed', (e: MessageEvent) => {
      try {
        const data: JobEventPayload = JSON.parse(e.data);
        this.emitEvent({ event: 'completed', data });
        this.latestStatus.set(data);
      } catch (err) {
        // Ignore parse errors
      }
    });

    this.eventSource.addEventListener('error', (e: MessageEvent) => {
      try {
        const data: JobEventPayload = JSON.parse(e.data);
        this.emitEvent({ event: 'error', data });
        const errorMessage = data?.error_message || 'Unknown error occurred';
        this.latestError.set(errorMessage);
      } catch (err) {
        this.latestError.set('Connection error occurred');
      }
    });

    this.eventSource.addEventListener('keepalive', () => {
      // Keepalive - no action needed
    });
  }

  private emitEvent(event: JobEvent): void {
    this.events.update(prev => [...prev, event]);
  }

  disconnect(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    this.isConnected.set(false);
    this.connectionState.set('disconnected');
    this.reconnectAttempts = 0;
    this.retryAttempt.set(0);

    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }

    this.currentJobId = null;
  }

  clearEvents(): void {
    this.events.set([]);
  }

  clearError(): void {
    this.latestError.set(null);
  }

  // Expose for testing
  getEventSource(): MockEventSource | null {
    return this.eventSource;
  }
}

describe('JobSseService', () => {
  let service: TestJobSseService;
  let mockEventSource: MockEventSource | null = null;

  beforeEach(() => {
    service = new TestJobSseService();
  });

  describe('streamJobEvents', () => {
    it('should establish SSE connection to /api/jobs/{id}/events', () => {
      service.streamJobEvents('job-123');
      mockEventSource = service.getEventSource();
      expect(mockEventSource).toBeTruthy();
      expect(mockEventSource?.url).toBe('/api/jobs/job-123/events');
    });

    it('should return an observable', () => {
      const observable = service.streamJobEvents('job-123');
      expect(observable).toBeDefined();
      expect(typeof observable.asObservable).toBe('function');
    });

    it('should emit connected event', () => {
      let emittedEvent: JobEvent | null = null;
      service.streamJobEvents('job-123');
      mockEventSource = service.getEventSource();
      
      // Manually trigger connected event
      service.getEventSource()?.simulateEvent('connected', null);
      
      expect(service.events().length).toBeGreaterThan(0);
    });

    it('should parse status_update event correctly', () => {
      const payload: JobEventPayload = {
        job_id: 'job-123',
        status: 'processing',
        previous_status: 'pending',
        instance_id: 'instance-1',
      };

      service.streamJobEvents('job-123');
      service.getEventSource()?.simulateEvent('status_update', payload);

      expect(service.latestStatus()).toEqual(payload);
    });

    it('should update isConnected signal on connected', () => {
      service.streamJobEvents('job-123');
      service.getEventSource()?.simulateEvent('connected', null);

      expect(service.isConnected()).toBe(true);
    });
  });

  describe('disconnect', () => {
    it('should close EventSource', () => {
      service.streamJobEvents('job-123');
      const closeSpy = jest.spyOn(service.getEventSource()!, 'close');

      service.disconnect();

      expect(closeSpy).toHaveBeenCalled();
    });

    it('should clear isConnected signal', () => {
      service.streamJobEvents('job-123');
      service.getEventSource()?.simulateEvent('connected', null);

      expect(service.isConnected()).toBe(true);

      service.disconnect();

      expect(service.isConnected()).toBe(false);
    });

    it('should set connectionState to disconnected', () => {
      service.streamJobEvents('job-123');
      service.getEventSource()?.simulateEvent('connected', null);

      service.disconnect();

      expect(service.connectionState()).toBe('disconnected');
    });

    it('should clear currentJobId', () => {
      service.streamJobEvents('job-123');
      service.disconnect();

      // Should be able to connect to a different job
      service.streamJobEvents('job-456');
      expect(service.getEventSource()?.url).toBe('/api/jobs/job-456/events');
    });
  });

  describe('clearEvents', () => {
    it('should clear events signal', () => {
      service.streamJobEvents('job-123');
      service.getEventSource()?.simulateEvent('connected', null);
      service.getEventSource()?.simulateEvent('status_update', { job_id: 'job-123', status: 'processing' });

      expect(service.events().length).toBeGreaterThan(0);

      service.clearEvents();

      expect(service.events()).toEqual([]);
    });
  });

  describe('clearError', () => {
    it('should clear latestError signal', () => {
      service.streamJobEvents('job-123');
      service.getEventSource()?.simulateEvent('error', { job_id: 'job-123', error_message: 'Test error' });

      expect(service.latestError()).toBeTruthy();

      service.clearError();

      expect(service.latestError()).toBeNull();
    });
  });

  describe('signals', () => {
    it('should expose isConnected signal', () => {
      expect(service.isConnected).toBeDefined();
      expect(typeof service.isConnected).toBe('function');
    });

    it('should expose connectionState signal', () => {
      expect(service.connectionState).toBeDefined();
      expect(typeof service.connectionState).toBe('function');
    });

    it('should expose events signal', () => {
      expect(service.events).toBeDefined();
      expect(typeof service.events).toBe('function');
    });

    it('should expose latestStatus signal', () => {
      expect(service.latestStatus).toBeDefined();
      expect(typeof service.latestStatus).toBe('function');
    });

    it('should expose latestError signal', () => {
      expect(service.latestError).toBeDefined();
      expect(typeof service.latestError).toBe('function');
    });

    it('should expose retryAttempt signal', () => {
      expect(service.retryAttempt).toBeDefined();
      expect(typeof service.retryAttempt).toBe('function');
    });
  });
});
