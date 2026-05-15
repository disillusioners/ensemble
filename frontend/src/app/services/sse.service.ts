import { Injectable, NgZone, signal } from '@angular/core';
import type { Message, SSEEvent, ToolCall } from '../models';

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
  statusChange = signal<{ instance_id: string; status: string } | null>(null);

  constructor(private ngZone: NgZone) {}

  /**
   * Append or update a message in the list with deduplication by message_id.
   * Messages are sorted by created_at to maintain correct chronological order.
   */
  private upsertMessage(message: Message): void {
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
          });
        } catch (err) {
          console.error('[SSE] Failed to parse status_change:', err);
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
