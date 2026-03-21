import { Injectable, NgZone, signal } from '@angular/core';
import { Observable, Subject } from 'rxjs';
import type { JobEvent, JobEventPayload } from '../models/job.model';

@Injectable({ providedIn: 'root' })
export class JobSseService {
  private readonly API_BASE = '/api';
  private readonly MAX_RECONNECT_ATTEMPTS = 5;
  private readonly INITIAL_RECONNECT_DELAY = 1000;
  private readonly MAX_RECONNECT_DELAY = 30000;

  private eventSource: EventSource | null = null;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private currentJobId: string | null = null;
  private eventSubject = new Subject<JobEvent>();
  private currentObserver: Observer<JobEvent> | null = null;

  // Signals for reactive state
  readonly isConnected = signal(false);
  readonly events = signal<JobEvent[]>([]);
  readonly latestStatus = signal<JobEventPayload | null>(null);
  readonly latestError = signal<string | null>(null);

  constructor(private ngZone: NgZone) {}

  /**
   * Connect to SSE stream for job events and return an Observable.
   * The Observable emits JobEvent objects as they arrive from the server.
   */
  streamJobEvents(jobId: string): Observable<JobEvent> {
    // If already connected to this job, return existing observable
    if (this.currentJobId === jobId && this.eventSource) {
      return this.eventSubject.asObservable();
    }

    this.disconnect();
    this.currentJobId = jobId;
    this.connectInternal();

    return this.eventSubject.asObservable();
  }

  private connectInternal(): void {
    if (!this.currentJobId) return;

    if (this.isConnected() && this.eventSource) {
      return;
    }

    const url = `${this.API_BASE}/jobs/${this.currentJobId}/events`;
    console.log('[JobSse] Creating EventSource with URL:', url);

    this.eventSource = new EventSource(url);

    this.eventSource.addEventListener('connected', () => {
      this.ngZone.run(() => {
        console.log('[JobSse] Connected to job:', this.currentJobId);
        this.reconnectAttempts = 0;
        this.isConnected.set(true);

        const event: JobEvent = {
          event: 'connected',
          data: null,
        };
        this.emitEvent(event);
      });
    });

    this.eventSource.addEventListener('status_update', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data: JobEventPayload = JSON.parse(e.data);
          const event: JobEvent = {
            event: 'status_update',
            data,
          };
          this.emitEvent(event);
          this.latestStatus.set(data);
          this.latestError.set(null);
        } catch (err) {
          console.error('[JobSse] Failed to parse status_update event:', err);
        }
      });
    });

    this.eventSource.addEventListener('completed', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data: JobEventPayload = JSON.parse(e.data);
          const event: JobEvent = {
            event: 'completed',
            data,
          };
          this.emitEvent(event);
          this.latestStatus.set(data);
          this.latestError.set(null);
        } catch (err) {
          console.error('[JobSse] Failed to parse completed event:', err);
        }
      });
    });

    this.eventSource.addEventListener('error', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data: JobEventPayload = JSON.parse(e.data);
          const event: JobEvent = {
            event: 'error',
            data,
          };
          this.emitEvent(event);

          const errorMessage = data.error_message || 'Unknown error occurred';
          this.latestError.set(errorMessage);
        } catch (err) {
          console.error('[JobSse] Failed to parse error event:', err);
          // Handle case where error data is not valid JSON
          this.latestError.set('Connection error occurred');
        }
      });
    });

    this.eventSource.addEventListener('keepalive', () => {
      // Keepalive received, connection is alive - no action needed
    });

    this.eventSource.onerror = () => {
      this.ngZone.run(() => {
        console.error('[JobSse] SSE connection error');
        this.isConnected.set(false);
        this.eventSource?.close();

        // Attempt reconnection with exponential backoff
        if (this.reconnectAttempts < this.MAX_RECONNECT_ATTEMPTS) {
          this.reconnectAttempts++;
          const delay = Math.min(
            this.INITIAL_RECONNECT_DELAY * Math.pow(2, this.reconnectAttempts - 1),
            this.MAX_RECONNECT_DELAY
          );
          console.log(`[JobSse] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.MAX_RECONNECT_ATTEMPTS})`);
          this.clearReconnectTimeout();
          this.reconnectTimeout = setTimeout(() => this.connectInternal(), delay);
        } else {
          console.error('[JobSse] Max reconnection attempts reached');
          this.latestError.set('Connection failed after multiple attempts');
        }
      });
    };
  }

  private emitEvent(event: JobEvent): void {
    this.events.update(prev => [...prev, event]);
    this.eventSubject.next(event);
  }

  private clearReconnectTimeout(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  /**
   * Disconnect from SSE stream and clean up resources.
   */
  disconnect(): void {
    this.clearReconnectTimeout();
    this.isConnected.set(false);
    this.reconnectAttempts = 0;

    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }

    this.currentJobId = null;
  }

  /**
   * Clear accumulated events.
   */
  clearEvents(): void {
    this.events.set([]);
  }

  /**
   * Clear error state.
   */
  clearError(): void {
    this.latestError.set(null);
  }
}

/**
 * Minimal Observer interface for RxJS Subject
 */
interface Observer<T> {
  next: (value: T) => void;
  error: (err: any) => void;
  complete: () => void;
}
