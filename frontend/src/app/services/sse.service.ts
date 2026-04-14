import { Injectable, NgZone, signal } from '@angular/core';
import type { Message, SSEEvent } from '../models';

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

  constructor(private ngZone: NgZone) {}

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
      });
    });

    // Checkpoint event - full message state snapshot
    eventSource.addEventListener('checkpoint', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          console.log('[SSE] checkpoint event received');
          
          this.events.update(evts => [...evts, { type: 'checkpoint', data }]);
          
          // Update messages from checkpoint data
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
          console.error('[SSE] Failed to parse checkpoint:', err);
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
   * Clears all event-related state.
   */
  clearEvents(): void {
    this.events.set([]);
    this.latestError.set(null);
    this.messages.set([]);
  }
}
