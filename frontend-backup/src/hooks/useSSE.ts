import { useEffect, useRef, useState } from 'preact/hooks';
import type { Message } from '../types';

// Event types from backend
export type EventType = 
  | 'connected' 
  | 'message_queued' 
  | 'status_changed' 
  | 'content_chunk' 
  | 'tool_call' 
  | 'completed' 
  | 'error' 
  | 'keepalive';

export interface SSEEvent {
  event_id: number;
  type: EventType;
  session_id: string;
  message_id: string | null;
  data: Record<string, unknown>;
}

export interface UseSSEReturn {
  isStreaming: boolean;
  events: SSEEvent[];
  latestCompletedMessage: Message | null;
  latestError: { message_id: string; error: string } | null;
  statusUpdates: Map<string, string>; // message_id -> status
}

const SSE_BASE = import.meta.env.PROD ? '' : '/api';
const MAX_RECONNECT_ATTEMPTS = 5;

export function useSSE(sessionId: string | null): UseSSEReturn {
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isConnectedRef = useRef(false);
  
  const [isStreaming, setIsStreaming] = useState(false);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [latestCompletedMessage, setLatestCompletedMessage] = useState<Message | null>(null);
  const [latestError, setLatestError] = useState<{ message_id: string; error: string } | null>(null);
  const [statusUpdates, setStatusUpdates] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    if (!sessionId) {
      return;
    }

    const clearReconnectTimeout = () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };

    const connect = () => {
      // Don't reconnect if already connected
      if (isConnectedRef.current && eventSourceRef.current) {
        return;
      }

      const eventSource = new EventSource(`${SSE_BASE}/sessions/${sessionId}/events`);
      eventSourceRef.current = eventSource;
      setIsStreaming(true);

      eventSource.addEventListener('connected', () => {
        console.log('SSE connected to session:', sessionId);
        reconnectAttemptsRef.current = 0;
        isConnectedRef.current = true;
      });

      eventSource.addEventListener('message_queued', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'message_queued',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          setEvents(prev => [...prev, event]);
          setStatusUpdates(prev => new Map(prev).set(data.message_id, 'queued'));
        } catch (err) {
          console.error('Failed to parse message_queued event:', err);
        }
      });

      eventSource.addEventListener('status_changed', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'status_changed',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          setEvents(prev => [...prev, event]);
          if (data.message_id && data.status) {
            setStatusUpdates(prev => new Map(prev).set(data.message_id, data.status));
          }
        } catch (err) {
          console.error('Failed to parse status_changed event:', err);
        }
      });

      eventSource.addEventListener('content_chunk', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'content_chunk',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          setEvents(prev => [...prev, event]);
        } catch (err) {
          console.error('Failed to parse content_chunk event:', err);
        }
      });

      eventSource.addEventListener('tool_call', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'tool_call',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          setEvents(prev => [...prev, event]);
        } catch (err) {
          console.error('Failed to parse tool_call event:', err);
        }
      });

      eventSource.addEventListener('completed', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'completed',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          setEvents(prev => [...prev, event]);
          
          // Create a Message object from the completed event
          if (data.message_id) {
            const message: Message = {
              type: 'message',
              message_id: data.message_id,
              role: 'assistant',
              content: data.content || '',
              thinking: data.thinking || undefined,
              tool_calls: data.tool_calls || undefined,
              created_at: new Date().toISOString(),
            };
            setLatestCompletedMessage(message);
            setStatusUpdates(prev => new Map(prev).set(data.message_id, 'completed'));
          }
        } catch (err) {
          console.error('Failed to parse completed event:', err);
        }
      });

      eventSource.addEventListener('error', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          const event: SSEEvent = {
            event_id: parseInt(e.lastEventId || '0'),
            type: 'error',
            session_id: data.session_id,
            message_id: data.message_id,
            data: data,
          };
          setEvents(prev => [...prev, event]);
          
          if (data.message_id && data.error) {
            setLatestError({ message_id: data.message_id, error: String(data.error) });
            setStatusUpdates(prev => new Map(prev).set(data.message_id, 'failed'));
          }
        } catch (err) {
          console.error('Failed to parse error event:', err);
        }
      });

      eventSource.addEventListener('keepalive', () => {
        // Keepalive received, connection is alive
      });

      eventSource.onerror = () => {
        console.error('SSE connection error');
        isConnectedRef.current = false;
        eventSource.close();
        setIsStreaming(false);
        
        // Attempt reconnection with exponential backoff
        if (reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
          reconnectAttemptsRef.current++;
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000);
          console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current})`);
          clearReconnectTimeout();
          reconnectTimeoutRef.current = setTimeout(connect, delay);
        } else {
          console.error('Max reconnection attempts reached');
        }
      };
    };

    // Initial connection
    connect();

    return () => {
      // Cleanup on unmount or sessionId change
      clearReconnectTimeout();
      isConnectedRef.current = false;
      reconnectAttemptsRef.current = 0;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setIsStreaming(false);
    };
  }, [sessionId]); // Only depend on sessionId, not connect function

  return { 
    isStreaming, 
    events, 
    latestCompletedMessage, 
    latestError,
    statusUpdates,
  };
}
