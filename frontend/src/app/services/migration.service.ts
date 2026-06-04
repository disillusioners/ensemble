import { Injectable, NgZone, inject, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap, catchError, of } from 'rxjs';
import type {
  MigrationAvailability,
  MigrationProgress,
  MigrationStartResponse,
  MigrationCancelResponse,
  MigrationLogEntry,
  MigrationStatus,
  MigrationDatabaseSwitchResponse,
} from '../models';

/**
 * Service for the SQLite → PostgreSQL database migration UI.
 *
 * Wires:
 *   GET  /api/migration/availability → check preconditions
 *   POST /api/migration/start        → kick off migration
 *   GET  /api/migration/status       → poll current progress
 *   POST /api/migration/cancel       → cooperative cancel
 *   GET  /api/migration/events       → SSE stream of live events
 *   POST /api/database/switch        → flip active database (sqlite ↔ postgres)
 *
 * SSE events update internal signals; the component template reads
 * signals directly. Connection state is exposed for UI affordances.
 */
@Injectable({ providedIn: 'root' })
export class MigrationService {
  private readonly http = inject(HttpClient);
  private readonly ngZone = inject(NgZone);

  private readonly API_BASE = '/api/migration';
  private readonly SWITCH_URL = '/api/database/switch';
  private eventSource: EventSource | null = null;
  private statusPollingInterval: ReturnType<typeof setInterval> | null = null;

  // ── Public signals ──────────────────────────────────────────────────────
  readonly availability = signal<MigrationAvailability | null>(null);
  readonly status = signal<MigrationProgress | null>(null);
  readonly logs = signal<MigrationLogEntry[]>([]);
  readonly isConnected = signal(false);

  // Computed convenience signals
  readonly isMigrating = computed(() => this.status()?.status === 'running');
  readonly isComplete = computed(() => this.status()?.status === 'completed');
  readonly isFailed = computed(() => this.status()?.status === 'failed');
  readonly isCancelled = computed(() => this.status()?.status === 'cancelled');
  readonly isIdle = computed(() => !this.status() || this.status()?.status === 'idle');

  readonly currentProgress = computed(() => {
    const s = this.status();
    if (!s || s.tables_total === 0) return 0;
    return Math.min(100, Math.round((s.tables_completed / s.tables_total) * 100));
  });

  /**
   * GET /api/migration/availability
   */
  checkAvailability(): Observable<MigrationAvailability | null> {
    return this.http.get<MigrationAvailability>(`${this.API_BASE}/availability`).pipe(
      tap(av => this.availability.set(av)),
      catchError(err => {
        console.error('Failed to check migration availability:', err);
        // Daemon unreachable: don't claim a specific backend. Render an
        // "unknown" state so the UI doesn't lie about which DB is active
        // (the old hard-coded "postgres" default was misleading and made
        // a down daemon look like a PG daemon).
        this.availability.set({
          migration_available: false,
          current_database: 'unknown',
          postgres_configured: false,
          can_start: false,
          postgres_env_set: false,
          can_switch: false,
        });
        return of(null);
      })
    );
  }

  /**
   * GET /api/migration/status
   */
  fetchStatus(): Observable<MigrationProgress> {
    return this.http.get<MigrationProgress>(`${this.API_BASE}/status`).pipe(
      tap(progress => {
        this.status.set(progress);
        // Resume polling if the migration is already running (e.g. after a page reload)
        if (progress.status === 'running') {
          this.startStatusPolling();
        }
      })
    );
  }

  /**
   * POST /api/migration/start
   */
  startMigration(): Observable<MigrationStartResponse> {
    return this.http.post<MigrationStartResponse>(`${this.API_BASE}/start`, {}).pipe(
      tap(response => {
        // Optimistically update status to running
        this.status.set({
          status: 'running',
          current_phase: 'starting',
          current_table: null,
          tables_completed: 0,
          tables_total: 0,
          checkpoints_migrated: 0,
          error: null,
          started_at: new Date().toISOString(),
          completed_at: null,
          requires_restart: false,
        });
        // Auto-connect to SSE once started
        this.connectEvents();
        // The SSE progress events only carry phase/message — they don't
        // include table counts, so we poll the status endpoint to keep
        // the progress bar in sync with the real backend state.
        this.startStatusPolling();
      })
    );
  }

  /**
   * POST /api/migration/cancel
   */
  cancelMigration(): Observable<MigrationCancelResponse> {
    return this.http.post<MigrationCancelResponse>(`${this.API_BASE}/cancel`, {}).pipe(
      tap(() => {
        // The cancel is cooperative — status will flip to 'cancelled' on
        // the SSE stream. Don't update status here.
      })
    );
  }

  /**
   * POST /api/database/switch
   *
   * Flip the active database. The endpoint accepts the target database
   * as a request body and returns a ``requires_restart`` flag indicating
   * whether the daemon must be restarted before the change takes effect.
   */
  switchDatabase(database: 'sqlite' | 'postgres'): Observable<MigrationDatabaseSwitchResponse> {
    return this.http.post<MigrationDatabaseSwitchResponse>(this.SWITCH_URL, { database });
  }

  /**
   * Connect to the SSE event stream. Idempotent: calling while already
   * connected is a no-op.
   */
  connectEvents(): void {
    if (this.eventSource) {
      return;
    }

    const url = `${this.API_BASE}/events`;
    const eventSource = new EventSource(url);
    this.eventSource = eventSource;

    // Flip isConnected to true as soon as the SSE handshake completes.
    // The backend doesn't emit a custom 'connected' event, so we rely on
    // the browser's native onopen callback.
    eventSource.onopen = () => {
      this.ngZone.run(() => {
        this.isConnected.set(true);
      });
    };

    eventSource.addEventListener('progress', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          this.handleProgressEvent(data);
        } catch (err) {
          console.error('[Migration SSE] Failed to parse progress event:', err);
        }
      });
    });

    eventSource.addEventListener('log', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          this.handleLogEvent(data);
        } catch (err) {
          console.error('[Migration SSE] Failed to parse log event:', err);
        }
      });
    });

    eventSource.addEventListener('complete', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          this.handleCompleteEvent(data);
        } catch (err) {
          console.error('[Migration SSE] Failed to parse complete event:', err);
        }
        this.handleTerminalEvent();
      });
    });

    eventSource.addEventListener('error', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          this.handleErrorEvent(data);
        } catch (err) {
          console.error('[Migration SSE] Failed to parse error event:', err);
        }
        this.handleTerminalEvent();
      });
    });

    eventSource.addEventListener('cancelled', (e: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(e.data);
          this.handleCancelledEvent(data);
        } catch (err) {
          console.error('[Migration SSE] Failed to parse cancelled event:', err);
        }
        this.handleTerminalEvent();
      });
    });

    eventSource.addEventListener('keepalive', () => {
      // Connection is alive, no action needed
    });

    eventSource.onerror = () => {
      this.ngZone.run(() => {
        if (this.eventSource) {
          console.error('[Migration SSE] Connection error');
          this.eventSource?.close();
          this.eventSource = null;
        }
        this.isConnected.set(false);
      });
    };
  }

  /**
   * Disconnect the SSE stream. Safe to call when not connected.
   */
  disconnectEvents(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.isConnected.set(false);
    this.stopStatusPolling();
  }

  /**
   * Clear the in-memory log buffer. Useful when starting a fresh migration.
   */
  clearLogs(): void {
    this.logs.set([]);
  }

  // ── SSE event handlers ─────────────────────────────────────────────────

  private handleProgressEvent(data: Record<string, unknown>): void {
    // SSE progress events only carry `{ phase, current_table?, message? }` —
    // they do NOT include table counts or checkpoint counts. Those fields are
    // kept fresh by the /status polling started in startMigration().
    // Here we only update phase/table info that the event actually contains.
    const current = this.status();
    this.status.set({
      ...(current ?? {
        status: 'running',
        current_phase: null,
        current_table: null,
        tables_completed: 0,
        tables_total: 0,
        checkpoints_migrated: 0,
        error: null,
        started_at: new Date().toISOString(),
        completed_at: null,
        requires_restart: false,
      }),
      status: 'running',
      current_phase: (data['phase'] as string) ?? current?.current_phase ?? null,
      current_table: (data['current_table'] as string) ?? current?.current_table ?? null,
    });
  }

  private handleLogEvent(data: Record<string, unknown>): void {
    const level = (data['level'] as MigrationLogEntry['level']) ?? 'info';
    const message = (data['message'] as string) ?? '';
    if (!message) {
      return;
    }
    this.logs.update(logs => [
      ...logs,
      {
        level,
        message,
        timestamp: (data['timestamp'] as string) ?? new Date().toISOString(),
      },
    ]);
  }

  private handleCompleteEvent(data: Record<string, unknown>): void {
    const current = this.status();
    this.status.set({
      status: 'completed',
      current_phase: null,
      current_table: null,
      tables_completed: (data['tables_migrated'] as number) ?? current?.tables_completed ?? 0,
      tables_total: current?.tables_total ?? (data['tables_migrated'] as number) ?? 0,
      checkpoints_migrated:
        (data['checkpoints_migrated'] as number) ?? current?.checkpoints_migrated ?? 0,
      error: null,
      started_at: current?.started_at ?? null,
      completed_at: new Date().toISOString(),
      requires_restart: true,
    });
  }

  private handleErrorEvent(data: Record<string, unknown>): void {
    const current = this.status();
    const errorMessage = (data['error'] as string) ?? (data['message'] as string) ?? 'Unknown error';
    this.status.set({
      status: 'failed',
      current_phase: current?.current_phase ?? null,
      current_table: current?.current_table ?? null,
      tables_completed: current?.tables_completed ?? 0,
      tables_total: current?.tables_total ?? 0,
      checkpoints_migrated: current?.checkpoints_migrated ?? 0,
      error: errorMessage,
      started_at: current?.started_at ?? null,
      completed_at: new Date().toISOString(),
      requires_restart: false,
    });
  }

  private handleCancelledEvent(_data: Record<string, unknown>): void {
    const current = this.status();
    this.status.set({
      status: 'cancelled',
      current_phase: null,
      current_table: null,
      tables_completed: current?.tables_completed ?? 0,
      tables_total: current?.tables_total ?? 0,
      checkpoints_migrated: current?.checkpoints_migrated ?? 0,
      error: null,
      started_at: current?.started_at ?? null,
      completed_at: new Date().toISOString(),
      requires_restart: false,
    });
  }

  private handleTerminalEvent(): void {
    // Server closes the SSE stream after complete/error/cancelled. Clean up.
    // Set eventSource to null BEFORE isConnected.set(false) so that the native
    // onerror callback (which fires when we close the connection) can detect
    // this is a clean shutdown and avoid logging a misleading error.
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.isConnected.set(false);
    this.stopStatusPolling();
  }

  // ── Status polling ─────────────────────────────────────────────────────
  // SSE progress events only carry phase/message, not table counts. We poll
  // /api/migration/status every 2s while a migration is running to keep
  // the progress bar in sync with the real backend state.

  private startStatusPolling(): void {
    this.stopStatusPolling();
    this.statusPollingInterval = setInterval(() => {
      this.http.get<MigrationProgress>(`${this.API_BASE}/status`).subscribe({
        next: (status) => this.status.set(status),
        error: () => {
          // Silently ignore — the next tick will retry, and SSE events still
          // provide phase updates while the request is failing.
        },
      });
    }, 2000);
  }

  private stopStatusPolling(): void {
    if (this.statusPollingInterval !== null) {
      clearInterval(this.statusPollingInterval);
      this.statusPollingInterval = null;
    }
  }
}
