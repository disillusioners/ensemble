import { Component, OnInit, OnDestroy, inject, DestroyRef, effect, viewChild, ElementRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MigrationService } from '../../services/migration.service';

/**
 * Database migration page (SQLite → PostgreSQL).
 *
 * Pure presentation: subscribes to MigrationService signals and forwards
 * user actions back to the service. The service owns the SSE stream and
 * the lifecycle of any background work.
 */
@Component({
  selector: 'app-migration',
  standalone: true,
  imports: [
    MatProgressBarModule,
    MatButtonModule,
    MatIconModule,
    MatSnackBarModule,
  ],
  templateUrl: './migration.component.html',
  styleUrl: './migration.component.scss'
})
export class MigrationComponent implements OnInit, OnDestroy {
  private readonly migrationService = inject(MigrationService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);

  // Service signals exposed to the template
  readonly availability = this.migrationService.availability;
  readonly status = this.migrationService.status;
  readonly logs = this.migrationService.logs;
  readonly currentProgress = this.migrationService.currentProgress;
  readonly isMigrating = this.migrationService.isMigrating;
  readonly isComplete = this.migrationService.isComplete;
  readonly isFailed = this.migrationService.isFailed;
  readonly isCancelled = this.migrationService.isCancelled;
  readonly isConnected = this.migrationService.isConnected;

  // Auto-scroll target for the log container
  private logContainer = viewChild<ElementRef<HTMLElement>>('logContainer');

  constructor() {
    // Auto-scroll effect: whenever logs change, scroll the log viewer
    // to the bottom so the latest entry stays visible.
    effect(() => {
      const logs = this.logs();
      const container = this.logContainer();
      if (container && logs.length > 0) {
        // Defer to the next macrotask so Angular has finished rendering
        // the new log entry into the DOM before we measure scrollHeight.
        setTimeout(() => {
          container.nativeElement.scrollTop = container.nativeElement.scrollHeight;
        }, 0);
      }
    });
  }

  ngOnInit(): void {
    this.migrationService.checkAvailability()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe();

    this.migrationService.fetchStatus()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (status) => {
          // If a migration is already running, resume the SSE stream
          if (status.status === 'running') {
            this.migrationService.connectEvents();
          }
        },
        error: () => {
          // Silently ignore — the page still renders even if status is unavailable
        }
      });
  }

  ngOnDestroy(): void {
    this.migrationService.disconnectEvents();
  }

  // ── User actions ───────────────────────────────────────────────────────

  startMigration(): void {
    this.migrationService.clearLogs();
    this.migrationService.startMigration()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.showInfo('Migration started');
        },
        error: (err) => {
          console.error('Failed to start migration:', err);
          this.showError(err?.error?.detail || 'Failed to start migration');
        }
      });
  }

  cancelMigration(): void {
    this.migrationService.cancelMigration()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.showInfo('Cancellation requested');
        },
        error: (err) => {
          console.error('Failed to cancel migration:', err);
          this.showError(err?.error?.detail || 'Failed to cancel migration');
        }
      });
  }

  // ── Display helpers ────────────────────────────────────────────────────

  protected phaseLabel(phase: string | null | undefined): string {
    if (!phase) return 'Preparing...';
    return this.humanizePhase(phase);
  }

  protected logTime(iso: string): string {
    try {
      return new Date(iso).toLocaleTimeString();
    } catch {
      return iso;
    }
  }

  protected logLevelClass(level: string): string {
    return `log-${level}`;
  }

  private humanizePhase(phase: string): string {
    const map: Record<string, string> = {
      starting: 'Starting',
      creating_pg_engine: 'Creating PostgreSQL engine',
      creating_schema: 'Creating PostgreSQL schema',
      backfilling_migrations: 'Backfilling migration history',
      pausing_writes: 'Pausing writes',
      migrating_tables: 'Migrating tables',
      migrating_checkpoints: 'Migrating checkpoints',
      validating: 'Validating data',
      updating_config: 'Updating configuration',
    };
    return map[phase] ?? phase;
  }

  private showInfo(message: string): void {
    this.snackBar.open(message, 'Close', {
      duration: 3000,
      panelClass: 'info-snackbar'
    });
  }

  private showError(message: string): void {
    this.snackBar.open(message, 'Dismiss', {
      duration: 5000,
      panelClass: 'error-snackbar'
    });
  }
}
