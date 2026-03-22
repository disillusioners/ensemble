import { Injectable, NgZone, signal, computed } from '@angular/core';
import { Observable, Subject } from 'rxjs';
import type { JobEvent, JobEventPayload } from '../models/job.model';

/** Connection states for UI display */
export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'retrying' | 'failed';

@Injectable({ providedIn: 'root' })
export class JobSseService {
  private readonly API_BASE = '/api';
  private readonly MAX_RECONNECT_ATTEMPTS = 5;
  private readonly INITIAL_RECONNECT_DELAY = 1000;
  private readonly MAX_RECONNECT_DELAY = 30000;
  private readonly ERROR_DEBOUNCE_MS = 2000; // Prevent error spam

  private eventSource: EventSource | null = null;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private currentJobId: string | null = null;
  private eventSubject = new Subject<JobEvent>();
  private currentObserver: Observer<JobEvent> | null = null;
  private errorDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  private lastErrorShown: string | null = null;

  // Signals for reactive state
  readonly isConnected = signal(false);
  readonly connectionState = signal<ConnectionState>('disconnected');
  readonly events = signal<JobEvent[]>([]);
  readonly latestStatus = signal<JobEventPayload | null>(null);
  readonly latestError = signal<string | null>(null);
  readonly retryAttempt = signal(0);

  // Computed for UI convenience
  readonly isRetrying = computed(() => this.connectionState() === 'retrying');
  readonly isFailed = computed(() => this.connectionState() === 'failed');

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

    // Set connecting state
    this.connectionState.set('connecting');

    const url = `${this.API_BASE}/jobs/${this.currentJobId}/events`;
    console.log('[JobSse] Creating EventSource with URL:', url);

    this.eventSource = new EventSource(url);

    this.eventSource.addEventListener('connected', () => {
      this.ngZone.run(() => {
        console.log('[JobSse] Connected to job:', this.currentJobId);
        this.reconnectAttempts = 0;
        this.retryAttempt.set(0);
        this.isConnected.set(true);
        this.connectionState.set('connected');
        this.clearErrorDebounce();

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
          this.retryAttempt.set(this.reconnectAttempts);
          this.connectionState.set('retrying');
          const delay = Math.min(
            this.INITIAL_RECONNECT_DELAY * Math.pow(2, this.reconnectAttempts - 1),
            this.MAX_RECONNECT_DELAY
          );
          console.log(`[JobSse] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.MAX_RECONNECT_ATTEMPTS})`);
          this.clearReconnectTimeout();
          this.reconnectTimeout = setTimeout(() => this.connectInternal(), delay);
        } else {
          console.error('[JobSse] Max reconnection attempts reached');
          this.connectionState.set('failed');
          this.retryAttempt.set(0);
          // Debounce the error message to prevent spam
          this.setDebouncedError('Unable to connect to server. Please check your connection and refresh the page.');
        }
      });
    };
  }

  /**
   * Set error with debouncing to prevent spam
   */
  private setDebouncedError(message: string): void {
    // Skip if same error is already being shown
    if (this.lastErrorShown === message && this.latestError() !== null) {
      return;
    }

    // Clear any existing timer
    this.clearErrorDebounce();

    this.lastErrorShown = message;
    this.errorDebounceTimer = setTimeout(() => {
      this.latestError.set(message);
    }, this.ERROR_DEBOUNCE_MS);
  }

  private clearErrorDebounce(): void {
    if (this.errorDebounceTimer) {
      clearTimeout(this.errorDebounceTimer);
      this.errorDebounceTimer = null;
    }
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
    this.clearErrorDebounce();
    this.isConnected.set(false);
    this.connectionState.set('disconnected');
    this.reconnectAttempts = 0;
    this.retryAttempt.set(0);
    this.lastErrorShown = null;

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
    this.clearErrorDebounce();
    this.latestError.set(null);
    this.lastErrorShown = null;
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
