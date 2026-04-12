import { Injectable, NgZone, signal } from '@angular/core';
import type { Message, SSEEvent, SSEEventEnvelope, EventType, MessageDelta } from '../models';

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
  private currentInstanceId: string | null = null;

  // Signals for reactive state
  isStreaming = signal(false);
  events = signal<SSEEvent[]>([]);
  latestError = signal<{ message_id: string; error: string; instance_id?: string | null } | null>(null);
  statusUpdates = signal<Map<string, string>>(new Map());
  titleUpdates = signal<{ instance_id: string; title: string } | null>(null);
  
  // Simplified: emit message deltas that components can use to update messages directly
  messageDeltas = signal<MessageDelta[]>([]);

  constructor(private ngZone: NgZone) {}

  /**
   * Validates that an event belongs to the current instance.
   */
  private isValidInstanceEvent(data: { instance_id?: string }): boolean {
    if (data.instance_id !== this.currentInstanceId) {
      console.warn(
        `[SSE] Ignoring event from wrong instance: ${data.instance_id} (current: ${this.currentInstanceId})`
      );
      return false;
    }
    return true;
  }

  /**
   * Emit a message delta to all subscribers.
   * Components can use these deltas to update their messages directly.
   */
  private emitDelta(delta: Omit<MessageDelta, 'timestamp'>): void {
    const fullDelta: MessageDelta = {
      ...delta,
      timestamp: new Date().toISOString(),
    };
    this.messageDeltas.update(prev => [...prev, fullDelta]);
  }

  /**
   * Connects to SSE stream for the specified instance.
   */
  connect(instanceId: string): void {
    console.log('[SSE] connect() called with instanceId:', instanceId);
    if (this.currentInstanceId === instanceId && this.isConnected && this.eventSource) {
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

    if (this.isConnected && this.eventSource) {
      return;
    }

    const url = `${this.API_BASE}/instances/${this.currentInstanceId}/events`;
    console.log('[SSE] Creating EventSource with URL:', url);
    const eventSource = new EventSource(url);
    this.eventSource = eventSource;
    this.isStreaming.set(true);

    // Connected
    eventSource.addEventListener('connected', () => {
      console.log('[SSE] Connected to instance:', this.currentInstanceId);
      this.reconnectAttempts = 0;
      this.isConnected = true;
    });

    // Message received - new message (user input, child reports, etc.)
    eventSource.addEventListener('message_received', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const raw = JSON.parse(e.data);
          // Support both new envelope format and legacy flat format
          const envelope = raw.message ? raw : { ...raw, message: raw };
          if (!this.isValidInstanceEvent(envelope)) return;
          console.log('[SSE] message_received:', envelope.message_id, 'source:', envelope.message?.source);
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'message_received',
            instance_id: envelope.instance_id,
            message_id: envelope.message_id,
            data: envelope,
          }]);
          
          // Emit delta for ChatComponent to add the message
          this.emitDelta({
            type: 'message_received',
            instance_id: envelope.instance_id,
            message_id: envelope.message_id,
            message: envelope.message,
            source: envelope.message?.source,
          });
        } catch (err) {
          console.error('[SSE] Failed to parse message_received:', err);
        }
      });
    });

    // Processing started - emit delta to add placeholder
    eventSource.addEventListener('processing_started', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          if (!this.isValidInstanceEvent(data)) return;
          console.log('[SSE] processing_started:', data.message_id);
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'status_changed',
            instance_id: data.instance_id,
            message_id: data.message_id,
            data,
          }]);
          this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'processing'));
          
          // Emit delta for ChatComponent
          this.emitDelta({
            type: 'processing_started',
            instance_id: data.instance_id,
            message_id: data.message_id,
          });
        } catch (err) {
          console.error('[SSE] Failed to parse processing_started:', err);
        }
      });
    });

    // Content chunk - emit delta to append content
    eventSource.addEventListener('content_chunk', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const raw = JSON.parse(e.data);
          // Support both new delta format and legacy flat format
          const envelope = raw.delta ? raw : { ...raw, delta: { type: 'chunk', content: raw.chunk } };
          if (!this.isValidInstanceEvent(envelope)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'content_chunk',
            instance_id: envelope.instance_id,
            message_id: envelope.message_id,
            data: envelope,
          }]);
          
          // Emit delta
          if (envelope.delta?.content) {
            this.emitDelta({
              type: 'content_chunk',
              instance_id: envelope.instance_id,
              message_id: envelope.message_id,
              content: envelope.delta.content,
            });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse content_chunk:', err);
        }
      });
    });

    // Thinking - emit delta
    eventSource.addEventListener('thinking', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const raw = JSON.parse(e.data);
          // Support both new delta format and legacy flat format
          const envelope = raw.delta ? raw : { ...raw, delta: { type: 'thinking', content: raw.content } };
          if (!this.isValidInstanceEvent(envelope)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'thinking',
            instance_id: envelope.instance_id,
            message_id: envelope.message_id,
            data: envelope,
          }]);
          
          if (envelope.delta?.content) {
            this.emitDelta({
              type: 'thinking',
              instance_id: envelope.instance_id,
              message_id: envelope.message_id,
              content: envelope.delta.content,
            });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse thinking:', err);
        }
      });
    });

    // Tool call - emit delta
    eventSource.addEventListener('tool_call', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const raw = JSON.parse(e.data);
          // Support both new delta format and legacy flat format
          const envelope = raw.delta ? raw : { 
            ...raw, 
            delta: { 
              type: 'tool_call', 
              tool_call: { id: raw.id, name: raw.name, arguments: raw.arguments } 
            } 
          };
          if (!this.isValidInstanceEvent(envelope)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'tool_call',
            instance_id: envelope.instance_id,
            message_id: envelope.message_id,
            data: envelope,
          }]);
          
          if (envelope.delta?.tool_call) {
            this.emitDelta({
              type: 'tool_call',
              instance_id: envelope.instance_id,
              message_id: envelope.message_id,
              tool_call: envelope.delta.tool_call,
            });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse tool_call:', err);
        }
      });
    });

    // Tool complete - emit delta
    eventSource.addEventListener('tool_complete', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const raw = JSON.parse(e.data);
          // Support both new delta format and legacy flat format
          const envelope = raw.delta ? raw : { 
            ...raw, 
            delta: { 
              type: 'tool_complete', 
              tool_call: { id: raw.id, name: raw.name, output: raw.output } 
            } 
          };
          if (!this.isValidInstanceEvent(envelope)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'tool_complete',
            instance_id: envelope.instance_id,
            message_id: envelope.message_id,
            data: envelope,
          }]);
          
          if (envelope.delta?.tool_call) {
            this.emitDelta({
              type: 'tool_complete',
              instance_id: envelope.instance_id,
              message_id: envelope.message_id,
              tool_call: envelope.delta.tool_call,
            });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse tool_complete:', err);
        }
      });
    });

    // Processing completed - emit delta
    eventSource.addEventListener('processing_completed', (e: MessageEvent) => {
      this.handleCompletedEvent(e, 'processing_completed');
    });
    
    eventSource.addEventListener('completed', (e: MessageEvent) => {
      this.handleCompletedEvent(e, 'completed');
    });

    // Processing failed - emit delta
    eventSource.addEventListener('error', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          if (!this.isValidInstanceEvent(data)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'error',
            instance_id: data.instance_id,
            message_id: data.message_id,
            data,
          }]);
          
          if (data.message_id && data.error) {
            this.latestError.set({ 
              message_id: data.message_id, 
              error: String(data.error),
              instance_id: data.instance_id || this.currentInstanceId 
            });
            this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'failed'));
            
            this.emitDelta({
              type: 'processing_failed',
              instance_id: data.instance_id,
              message_id: data.message_id,
              error: String(data.error),
            });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse error:', err);
        }
      });
    });

    // Cancelled
    eventSource.addEventListener('cancelled', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          if (!this.isValidInstanceEvent(data)) return;
          console.log('[SSE] Received cancelled event');
          this.isStreaming.set(false);
          
          if (data.message_id) {
            this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'cancelled'));
          }
        } catch (err) {
          console.error('[SSE] Failed to parse cancelled:', err);
        }
      });
    });

    // Keepalive
    eventSource.addEventListener('keepalive', () => {
      // Connection is alive
    });

    // Title updated
    eventSource.addEventListener('title_updated', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          if (!this.isValidInstanceEvent(data)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'title_updated',
            instance_id: data.instance_id,
            message_id: data.message_id,
            data,
          }]);
          
          if (data.instance_id && data.title) {
            this.titleUpdates.set({ instance_id: data.instance_id, title: data.title });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse title_updated:', err);
        }
      });
    });

    // Message completed - final canonical message to replace accumulated streaming state
    eventSource.addEventListener('message_completed', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const raw = JSON.parse(e.data);
          // Support both new envelope format and legacy flat format
          const envelope = raw.message ? raw : { ...raw, message: raw.message };
          if (!this.isValidInstanceEvent(envelope)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'message_completed',
            instance_id: envelope.instance_id,
            message_id: envelope.message_id || raw.original_message_id,
            data: envelope,
          }]);
          
          // Emit delta with canonical message for ChatComponent
          if (envelope.message) {
            this.emitDelta({
              type: 'message_completed',
              instance_id: envelope.instance_id,
              message_id: envelope.message_id,
              message: envelope.message,
              original_message_id: envelope.message_id,
            });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse message_completed:', err);
        }
      });
    });

    // Error handling (connection errors - NOT SSE event type 'error')
    // Note: We must add explicit 'error' event listener BEFORE onerror handler
    // to prevent SSE 'event: error' messages from triggering reconnection
    eventSource.addEventListener('error', (e: MessageEvent) => {
      // This handles SSE event type 'error', not connection errors
      this.ngZone.run(() => {
        try {
          const raw = JSON.parse(e.data);
          const envelope = raw.status ? raw : { ...raw, status: raw };
          if (!this.isValidInstanceEvent(envelope)) return;
          
          this.events.update(prev => [...prev, {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'error',
            instance_id: envelope.instance_id,
            message_id: envelope.status?.message_id || null,
            data: envelope,
          }]);
          
          if (envelope.status?.error) {
            this.latestError.set({
              message_id: envelope.status.message_id,
              error: String(envelope.status.error),
              instance_id: envelope.instance_id
            });
            
            this.emitDelta({
              type: 'processing_failed',
              instance_id: envelope.instance_id,
              message_id: envelope.status.message_id,
              error: String(envelope.status.error),
            });
          }
        } catch (err) {
          console.error('[SSE] Failed to parse error event:', err);
        }
      });
    });
    
    eventSource.onerror = (error) => {
      console.error('[SSE] EventSource connection error:', error);
      this.isConnected = false;
      
      if (this.reconnectAttempts < this.MAX_RECONNECT_ATTEMPTS) {
        this.reconnectAttempts++;
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
        console.log(`[SSE] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        
        this.reconnectTimeout = setTimeout(() => {
          if (this.currentInstanceId) {
            this.connectInternal();
          }
        }, delay);
      } else {
        console.error('[SSE] Max reconnect attempts reached');
      }
    };
  }

  private handleCompletedEvent(e: MessageEvent, eventType: 'processing_completed' | 'completed'): void {
    this.ngZone.run(() => {
      try {
        const data = JSON.parse(e.data);
        if (!this.isValidInstanceEvent(data)) return;
        console.log(`[SSE] Received ${eventType} for message:`, data.message_id);
        
        this.events.update(prev => [...prev, {
          event_id: parseInt(e.lastEventId || '0'),
          type: eventType,
          instance_id: data.instance_id,
          message_id: data.message_id,
          data,
        }]);
        
        if (data.message_id) {
          this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'completed'));
          
          // Emit processing_completed delta
          this.emitDelta({
            type: 'processing_completed',
            instance_id: data.instance_id,
            message_id: data.message_id,
            success: data.success !== false,
          });
        }
        
        // Reset streaming state when completed
        console.log('[SSE] Setting isStreaming to false');
        this.isStreaming.set(false);
      } catch (err) {
        console.error(`[SSE] Failed to parse ${eventType}:`, err);
      }
    });
  }

  disconnect(): void {
    console.log('[SSE] disconnect() called');
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    
    this.isConnected = false;
    this.currentInstanceId = null;
    this.isStreaming.set(false);
  }

  /**
   * Clears all event-related state.
   */
  clearEvents(): void {
    this.events.set([]);
    this.latestError.set(null);
    this.statusUpdates.set(new Map());
    this.messageDeltas.set([]);
    this.titleUpdates.set(null);
  }
}
