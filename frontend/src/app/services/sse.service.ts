import { Injectable, NgZone, signal, computed, effect } from '@angular/core';
import type { Message, SSEEvent, EventType } from '../models';

@Injectable({
  providedIn: 'root'
})
export class SseService {
  private readonly API_BASE = '/api';
  private readonly MAX_RECONNECT_ATTEMPTS = 5;
  
  private eventSource: EventSource | null = null;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private isConnected = false;
  private currentSessionId: string | null = null;

  // Signals for reactive state
  isStreaming = signal(false);
  events = signal<SSEEvent[]>([]);
  latestCompletedMessage = signal<Message | null>(null);
  latestError = signal<{ message_id: string; error: string } | null>(null);
  statusUpdates = signal<Map<string, string>>(new Map());
  partialMessages = signal<Map<string, Message>>(new Map());

  constructor(private ngZone: NgZone) {}

  private _createEmptyMessage(messageId: string, sessionId: string): Message {
    return {
      type: 'message',
      message_id: messageId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
    };
  }

  connect(sessionId: string): void {
    if (this.currentSessionId === sessionId && this.isConnected && this.eventSource) {
      return;
    }

    this.disconnect();
    this.currentSessionId = sessionId;
    this.connectInternal();
  }

  private connectInternal(): void {
    if (!this.currentSessionId) return;

    if (this.isConnected && this.eventSource) {
      return;
    }

    const eventSource = new EventSource(`${this.API_BASE}/sessions/${this.currentSessionId}/events`);
    this.eventSource = eventSource;
    this.isStreaming.set(true);

    eventSource.addEventListener('connected', () => {
      console.log('SSE connected to session:', this.currentSessionId);
      this.reconnectAttempts = 0;
      this.isConnected = true;
    });

    eventSource.addEventListener('message_queued', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'message_queued',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
          this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'queued'));
        } catch (err) {
          console.error('Failed to parse message_queued event:', err);
        }
      });
    });

    eventSource.addEventListener('status_changed', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'status_changed',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
          if (data.message_id && data.status) {
            this.statusUpdates.update(prev => new Map(prev).set(data.message_id, data.status));
          }
        } catch (err) {
          console.error('Failed to parse status_changed event:', err);
        }
      });
    });

    eventSource.addEventListener('content_chunk', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'content_chunk',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
        } catch (err) {
          console.error('Failed to parse content_chunk event:', err);
        }
      });
    });

    eventSource.addEventListener('tool_call', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'tool_call',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
        } catch (err) {
          console.error('Failed to parse tool_call event:', err);
        }
      });
    });

    eventSource.addEventListener('thinking', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'thinking',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
          
          // Update or create a partial message with thinking
          if (data.message_id) {
            this.partialMessages.update(prev => {
              const existing = prev.get(data.message_id) || this._createEmptyMessage(data.message_id, data.session_id);
              const updated = {
                ...existing,
                thinking: data.content || existing.thinking,
              };
              return new Map(prev).set(data.message_id, updated);
            });
          }
        } catch (err) {
          console.error('Failed to parse thinking event:', err);
        }
      });
    });

    eventSource.addEventListener('tool_complete', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'tool_complete',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
          
          // Add tool to partial message
          if (data.message_id) {
            this.partialMessages.update(prev => {
              const existing = prev.get(data.message_id) || this._createEmptyMessage(data.message_id, data.session_id);
              const newToolCall = {
                id: data.id || `tool-${Date.now()}`,
                name: data.name || '',
                arguments: {},
                output: data.output || '',
              };
              const updated = {
                ...existing,
                tool_calls: [...(existing.tool_calls || []), newToolCall],
              };
              return new Map(prev).set(data.message_id, updated);
            });
          }
        } catch (err) {
          console.error('Failed to parse tool_complete event:', err);
        }
      });
    });

    eventSource.addEventListener('completed', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'completed',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
          
          // Create a Message object from the completed event
          if (data.message_id) {
            // Get the partial message that was built during streaming
            const partialMessage = this.partialMessages().get(data.message_id);
            
            // Use tool_calls from partial message (which has outputs) if available,
            // otherwise fall back to completed event data
            const toolCalls = partialMessage?.tool_calls || data.tool_calls || undefined;
            
            const message: Message = {
              type: 'message',
              message_id: data.message_id,
              role: 'assistant',
              content: data.content || '',
              thinking: data.thinking || undefined,
              thinking_extracted: data.thinking_extracted || undefined,
              tool_calls: toolCalls,
              created_at: new Date().toISOString(),
            };
            this.latestCompletedMessage.set(message);
            this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'completed'));
            
            // Clear partial message on completion
            this.partialMessages.update(prev => {
              const updated = new Map(prev);
              updated.delete(data.message_id);
              return updated;
            });
          }
        } catch (err) {
          console.error('Failed to parse completed event:', err);
        }
      });
    });

    eventSource.addEventListener('error', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'error',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          this.events.update(prev => [...prev, event]);
          
          if (data.message_id && data.error) {
            this.latestError.set({ message_id: data.message_id, error: String(data.error) });
            this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'failed'));
          }
        } catch (err) {
          console.error('Failed to parse error event:', err);
        }
      });
    });

    eventSource.addEventListener('keepalive', () => {
      // Keepalive received, connection is alive
    });

    eventSource.onerror = () => {
      this.ngZone.run(() => {
        console.error('SSE connection error');
        this.isConnected = false;
        eventSource.close();
        this.isStreaming.set(false);
        
        // Attempt reconnection with exponential backoff
        if (this.reconnectAttempts < this.MAX_RECONNECT_ATTEMPTS) {
          this.reconnectAttempts++;
          const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
          console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
          this.clearReconnectTimeout();
          this.reconnectTimeout = setTimeout(() => this.connectInternal(), delay);
        } else {
          console.error('Max reconnection attempts reached');
        }
      });
    };
  }

  private clearReconnectTimeout(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  disconnect(): void {
    this.clearReconnectTimeout();
    this.isConnected = false;
    this.reconnectAttempts = 0;
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.isStreaming.set(false);
    this.currentSessionId = null;
  }

  clearEvents(): void {
    this.events.set([]);
    this.latestCompletedMessage.set(null);
    this.latestError.set(null);
    this.statusUpdates.set(new Map());
  }
}
