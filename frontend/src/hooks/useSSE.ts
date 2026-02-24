import { useEffect, useRef, useState } from 'preact/hooks';
import type { Message } from '../types';

interface UseSSEReturn {
  isStreaming: boolean;
  latestMessage: Message | null;
}

const SSE_BASE = import.meta.env.PROD ? '' : '/api';

export function useSSE(sessionId: string | null): UseSSEReturn {
  const eventSourceRef = useRef<EventSource | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [latestMessage, setLatestMessage] = useState<Message | null>(null);

  useEffect(() => {
    if (!sessionId) {
      return;
    }

    setIsStreaming(true);
    const eventSource = new EventSource(`${SSE_BASE}/sessions/${sessionId}/events`);
    eventSourceRef.current = eventSource;

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'message') {
          setLatestMessage(data);
        }
      } catch (e) {
        console.error('Failed to parse SSE message:', e);
      }
    };

    eventSource.onerror = () => {
      setIsStreaming(false);
      eventSource.close();
    };

    eventSource.addEventListener('connected', () => {
      console.log('SSE connected');
    });

    return () => {
      eventSource.close();
      setIsStreaming(false);
    };
  }, [sessionId]);

  return { isStreaming, latestMessage };
}
