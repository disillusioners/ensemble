import { Component, OnInit, OnDestroy, inject, DestroyRef, effect, viewChild, ElementRef, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule, MatDialogRef, MatDialogActions, MatDialogContent, MatDialogTitle, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MigrationService } from '../../services/migration.service';

interface SwitchConfirmDialogData {
  target: 'SQLite' | 'PostgreSQL';
}

/**
 * Confirmation dialog shown before flipping the active database.
 *
 * Kept inline (rather than a standalone file) because it's a
 * single-purpose component used only by the migration page and the
 * caller only needs to read the boolean ``confirmed`` result.
 */
@Component({
  selector: 'app-switch-confirm-dialog',
  standalone: true,
  imports: [MatDialogModule, MatDialogTitle, MatDialogContent, MatDialogActions, MatButtonModule, MatIconModule],
  template: `
    <h2 mat-dialog-title>Switch to {{ data.target }}?</h2>
    <mat-dialog-content>
      <p>
        The active database will be flipped to <strong>{{ data.target }}</strong>.
        Restart the daemon for the change to take effect.
      </p>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button (click)="dialogRef.close(false)">Cancel</button>
      <button mat-raised-button color="primary" (click)="dialogRef.close(true)">
        <mat-icon>swap_horiz</mat-icon>
        Switch
      </button>
    </mat-dialog-actions>
  `,
})
export class SwitchConfirmDialog {
  readonly dialogRef = inject<MatDialogRef<SwitchConfirmDialog>>(MatDialogRef);
  readonly data = inject<SwitchConfirmDialogData>(MAT_DIALOG_DATA);
}

/**
 * Database page (SQLite ↔ PostgreSQL).
 *
 * Renders one of four states based on the live ``availability`` signal:
 *   A. SQLite + PG env set      → existing migration flow
 *   B. PostgreSQL active        → show current status + "Switch to SQLite"
 *   C. SQLite + PG env not set  → "PostgreSQL not configured" (no action)
 *   D. Unknown (daemon down)    → "Daemon unreachable" (no claim about backend)
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
  private readonly dialog = inject(MatDialog);
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

  // Switch-flow state. When non-null, the UI shows the post-switch
  // "restart required" confirmation instead of the switch action button.
  readonly switchedTo = signal<'SQLite' | 'PostgreSQL' | null>(null);
  readonly switching = signal(false);

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
    this.switchedTo.set(null);
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

  /**
   * State B action: operator is on PostgreSQL and wants to switch back
   * to SQLite. Opens a confirmation dialog first; on confirm, calls
   * the backend and surfaces the "restart required" message.
   */
  switchToSqlite(): void {
    this.openSwitchDialog('SQLite').then(confirmed => {
      if (!confirmed) {
        return;
      }
      this.switching.set(true);
      this.migrationService.switchDatabase('sqlite')
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          next: (response) => {
            this.switching.set(false);
            this.switchedTo.set('SQLite');
            this.showInfo(response.message || 'Database switched. Restart required for changes to take effect.');
          },
          error: (err) => {
            this.switching.set(false);
            console.error('Failed to switch database:', err);
            this.showError(err?.error?.detail || 'Failed to switch database');
          }
        });
    });
  }

  /**
   * State A action (post-migration, if the operator wants to flip back
   * to SQLite without running another migration). Mirrors switchToSqlite
   * but targets PostgreSQL.
   */
  switchToPostgres(): void {
    this.openSwitchDialog('PostgreSQL').then(confirmed => {
      if (!confirmed) {
        return;
      }
      this.switching.set(true);
      this.migrationService.switchDatabase('postgres')
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          next: (response) => {
            this.switching.set(false);
            this.switchedTo.set('PostgreSQL');
            this.showInfo(response.message || 'Database switched. Restart required for changes to take effect.');
          },
          error: (err) => {
            this.switching.set(false);
            console.error('Failed to switch database:', err);
            this.showError(err?.error?.detail || 'Failed to switch database');
          }
        });
    });
  }

  private openSwitchDialog(target: 'SQLite' | 'PostgreSQL'): Promise<boolean> {
    const ref = this.dialog.open(SwitchConfirmDialog, {
      data: { target },
      autoFocus: 'first-tabbable',
    });
    return new Promise<boolean>(resolve => {
      ref.afterClosed().subscribe(result => resolve(result === true));
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
