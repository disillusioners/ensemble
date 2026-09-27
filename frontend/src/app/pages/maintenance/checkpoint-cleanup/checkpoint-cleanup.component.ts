import {
  Component,
  DestroyRef,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { Subscription } from 'rxjs';
import { CheckpointCleanupService } from './checkpoint-cleanup.service';
import { ConfirmDialogComponent } from '../../../components/confirm-dialog/confirm-dialog.component';
import type {
  CheckpointCleanupBlobsSummary,
  CheckpointCleanupDryRun,
  CheckpointCleanupExecute,
  CheckpointCleanupExecuteRequest,
  CheckpointCleanupRun,
  CheckpointCleanupSkippedEntry,
  CheckpointCleanupStatus,
  MaintenanceErrorBody,
} from '../../../models';

/**
 * Checkpoint Cleanup section — full UI.
 *
 * Renders 4 cards (status, dry-run, execute, error) + a result panel
 * after execute completes. Subscribes to the colocated service
 * (`CheckpointCleanupService`) and forwards user actions back.
 *
 * AM-14 — 409-adoption: on 409 from `service.execute()`, adopt
 * `details.run_id` and resume polling (no error toast).
 * AM-11 — dual-flavor branch on `summary.blobs.destructive:bool`.
 * AM-10 — `skipped[]` render with summary line + reason badge map
 * (`ZERO_REFS_FAIL_SAFE` / `MAX_REFS_EXCEEDED` / `ERROR:*`) +
 * `skipped_truncated` notice.
 * AM-6 — interrupted-state affordance ("Daemon restarted mid-run —
 * re-run to converge").
 * AM-12 — `expected_duration_ms_hint` display in the executing card.
 * AM-13 — `maintenance_disabled` global banner above all cards.
 * AM-17 — execute payload is EXACTLY `{dry_run_run_id, expected_bytes,
 * confirm: true}` — NO `idempotency_key`, NO `crypto.randomUUID`.
 * AM-16 — honest duration copy ("May take several minutes on large
 * databases") + `fresh_until` rendered in the dry-run result panel.
 */
@Component({
  selector: 'app-checkpoint-cleanup',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    MatDialogModule,
  ],
  templateUrl: './checkpoint-cleanup.component.html',
  styleUrl: './checkpoint-cleanup.component.scss',
})
export class CheckpointCleanupComponent implements OnInit, OnDestroy {
  private readonly service = inject(CheckpointCleanupService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);

  // ── Service signals re-exposed for the template ───────────────────────
  readonly status = this.service.status;
  readonly lastDryRun = this.service.lastDryRun;
  readonly lastError = this.service.lastError;
  readonly canDryRun = this.service.canDryRun;
  readonly isRunInFlight = this.service.isRunInFlight;
  readonly isReady = this.service.isReady;

  // ── Local UI state ────────────────────────────────────────────────────
  readonly dryRunning = signal(false);
  readonly executing = signal(false);
  /** AM-12 — display hint from the 202 body. null until first 202 received. */
  readonly expectedDurationHintMs = signal<number | null>(null);
  /** Last completed polled run (set on `pollRun` terminal emission). */
  readonly lastExecuteResult = signal<CheckpointCleanupRun | null>(null);
  readonly activeRunId = signal<string | null>(null);
  /** Whether the dry-run is stale (drives the warning border on the dry-run button). */
  readonly dryRunIsStale = computed(() =>
    this.service.isDryRunStale(this.lastDryRun()),
  );

  /** Internal flag: did the execute fail because of a stale dry-run? */
  private staleDryRunBlocked = false;

  // Polling subscription — track so OnDestroy can tear down
  private pollSub: Subscription | null = null;

  ngOnInit(): void {
    this.refreshStatus();
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }

  // ── Actions ───────────────────────────────────────────────────────────

  refreshStatus(): void {
    this.service.fetchStatus().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      error: () => {
        // Already surfaced via service.lastError.
      },
    });
  }

  onDryRun(): void {
    if (!this.canDryRun() || this.dryRunning()) {
      return;
    }
    this.dryRunning.set(true);
    this.service.dryRun().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.dryRunning.set(false);
      },
      error: () => {
        this.dryRunning.set(false);
        // Surfaced via service.lastError; the inline banner renders it.
        this.showSnackForLastError();
      },
    });
  }

  onExecute(): void {
    const dryRun = this.lastDryRun();
    // Pre-flight: must have a dry-run AND it must be fresh.
    if (!dryRun) {
      this.snackBar.open(
        'Run the dry-run check first.',
        'Dismiss',
        { duration: 4000, panelClass: 'error-snackbar' },
      );
      return;
    }
    if (this.service.isDryRunStale(dryRun)) {
      this.staleDryRunBlocked = true;
      this.snackBar.open(
        'Dry-run is stale — re-run the check before executing.',
        'Dismiss',
        { duration: 5000, panelClass: 'error-snackbar' },
      );
      return;
    }
    this.staleDryRunBlocked = false;

    const ref = this.dialog.open(ConfirmDialogComponent, {
      panelClass: 'dark-modal-panel',
      data: {
        title: 'Run checkpoint cleanup',
        message: this.buildConfirmMessage(dryRun),
        confirmLabel: 'Cleanup now',
        cancelLabel: 'Cancel',
        destructive: true,
      },
    });

    ref.afterClosed().subscribe((confirmed: boolean | undefined) => {
      // AM-16 — `false | undefined` is cancel; only `true` proceeds.
      if (!confirmed) {
        return;
      }
      this.performExecute(dryRun);
    });
  }

  private performExecute(dryRun: CheckpointCleanupDryRun): void {
    if (this.executing()) {
      return; // belt + braces against double-click
    }
    this.executing.set(true);
    this.lastExecuteResult.set(null);
    this.expectedDurationHintMs.set(null);

    // AM-17 — payload is EXACTLY `{dry_run_run_id, expected_bytes,
    // confirm: true}` — NO idempotency key, NO client-side UUID generation.
    const payload: CheckpointCleanupExecuteRequest = {
      dry_run_run_id: dryRun.run_id,
      expected_bytes: dryRun.would_free_bytes,
      confirm: true,
    };

    this.service.execute(payload).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (body: CheckpointCleanupExecute) => {
        this.activeRunId.set(body.run_id);
        this.expectedDurationHintMs.set(body.expected_duration_ms_hint);
        this.startPolling(body.run_id);
      },
      error: (err: MaintenanceErrorBody) => {
        // AM-14, AM-17 — 409-adoption. If `run_in_flight` carries a
        // `details.run_id`, adopt it and resume polling — NO error
        // toast AND no inline error banner. The 409 response IS the
        // de-facto idempotency handle; we clear `lastError` so the
        // banner doesn't render during the "ride along" polling.
        const adoptedRunId = this.service.adoptRunIdFromError(err);
        if (adoptedRunId) {
          this.service.clearLastError();
          this.activeRunId.set(adoptedRunId);
          // No snack-bar for this case — ride along UX.
          this.startPolling(adoptedRunId);
          return;
        }
        // Fallthrough: malformed body / different error — surface.
        this.executing.set(false);
        this.showSnackForLastError();
      },
    });
  }

  private startPolling(runId: string): void {
    // AM-6 — poll terminates on `succeeded | failed | interrupted`.
    this.pollSub?.unsubscribe();
    this.pollSub = this.service.pollRun(runId).subscribe({
      next: (run: CheckpointCleanupRun) => {
        this.lastExecuteResult.set(run);
        if (
          run.status === 'succeeded' ||
          run.status === 'failed' ||
          run.status === 'interrupted'
        ) {
          this.executing.set(false);
          this.activeRunId.set(null);
          this.pollSub?.unsubscribe();
          this.pollSub = null;
          // Re-fetch status so `last_run` reflects the new row.
          this.refreshStatus();
          if (run.status === 'succeeded') {
            this.snackBar.open('Cleanup succeeded.', 'Dismiss', {
              duration: 4000,
              panelClass: 'success-snackbar',
            });
          } else if (run.status === 'failed') {
            this.snackBar.open(
              `Cleanup failed: ${run.error?.message ?? 'unknown error'}`,
              'Dismiss',
              { duration: 6000, panelClass: 'error-snackbar' },
            );
          }
          // AM-6 — `interrupted` has no toast (the result panel renders
          // the inline warning card with the re-run affordance).
        }
      },
      error: (err: MaintenanceErrorBody) => {
        this.executing.set(false);
        this.activeRunId.set(null);
        this.pollSub?.unsubscribe();
        this.pollSub = null;
        if (err?.error === 'not_found') {
          this.snackBar.open(
            'The run record was not found — re-run from the status page.',
            'Dismiss',
            { duration: 6000, panelClass: 'error-snackbar' },
          );
        } else {
          this.snackBar.open(
            err?.message ?? 'Polling failed.',
            'Dismiss',
            { duration: 6000, panelClass: 'error-snackbar' },
          );
        }
      },
    });
  }

  dismissError(): void {
    this.service.clearLastError();
  }

  // ── Display helpers (pure; protected for template) ─────────────────────

  /** AM-11 — branch on `destructive:bool` to pick the active flavor. */
  blobCountFor(summary: CheckpointCleanupBlobsSummary): { label: string; value: number } {
    if (summary.destructive) {
      return { label: 'Blobs deleted', value: summary.deleted ?? 0 };
    }
    return { label: 'Would delete (blobs)', value: summary.would_delete_count };
  }

  /** AM-11 — paired byte label. */
  blobBytesFor(summary: CheckpointCleanupBlobsSummary): { label: string; value: number } {
    if (summary.destructive) {
      return { label: 'Bytes freed', value: summary.bytes_freed ?? 0 };
    }
    return { label: 'Would free', value: summary.would_free_bytes };
  }

  /** AM-10 — reason → human label + tone. */
  skippedReasonLabel(reason: string): { label: string; tone: 'safe' | 'limit' | 'error' } {
    if (reason === 'ZERO_REFS_FAIL_SAFE') {
      return { label: 'Fail-safe (no refs)', tone: 'safe' };
    }
    if (reason === 'MAX_REFS_EXCEEDED') {
      return { label: 'Ref cap exceeded', tone: 'limit' };
    }
    if (reason.startsWith('ERROR:')) {
      return { label: `Error: ${reason.slice('ERROR:'.length)}`, tone: 'error' };
    }
    return { label: reason, tone: 'error' };
  }

  /**
   * A-8 — error code → human label for the inline banner. Only codes
   * with a curated label are mapped; everything else renders verbatim
   * (the raw code string), preserving the pre-existing behavior for
   * the 10 stable codes.
   */
  errorLabel(code: string): string {
    if (code === 'internal_error') {
      return 'Internal server error';
    }
    return code;
  }

  /** AM-6 — interrupted-state render guard. */
  canRerunInterrupted(run: CheckpointCleanupRun): boolean {
    return run.status === 'interrupted';
  }

  /** T6.3 pin target: `formatBytes` source MUST contain `1024` (binary). */
  formatBytes(n: number): string {
    if (typeof n !== 'number' || n < 0 || !Number.isFinite(n)) {
      return '0 B';
    }
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = n;
    let i = 0;
    // Binary divisor — 1024 — pinned by source-grep.
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i++;
    }
    const formatted = value >= 100 || i === 0 ? value.toFixed(0) : value.toFixed(1);
    return `${formatted} ${units[i]}`;
  }

  /** Honest-duration copy: ms → human. "412ms", "1.8s", "2m 14s". */
  formatDuration(ms: number | null | undefined): string {
    if (ms == null || !Number.isFinite(ms) || ms < 0) {
      return '—';
    }
    if (ms < 1000) {
      return `${Math.round(ms)}ms`;
    }
    if (ms < 60_000) {
      return `${(ms / 1000).toFixed(1)}s`;
    }
    const totalSec = Math.floor(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    if (m < 60) {
      return s > 0 ? `${m}m ${s}s` : `${m}m`;
    }
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
  }

  /** ISO timestamp → locale string. `+00:00` and `Z` parse equivalently (A-7). */
  formatTimestamp(iso: string | null | undefined): string {
    if (!iso) {
      return '—';
    }
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  // ── Private helpers ───────────────────────────────────────────────────

  private buildConfirmMessage(dryRun: CheckpointCleanupDryRun): string {
    const bytes = this.formatBytes(dryRun.would_free_bytes);
    const rows = dryRun.would_delete.checkpoint_rows;
    return (
      `This will permanently delete ~${bytes} of unreferenced blobs and ` +
      `${rows} excess checkpoint rows. This may take several minutes on ` +
      `large databases. This cannot be undone.`
    );
  }

  private showSnackForLastError(): void {
    const err = this.lastError();
    if (!err) {
      return;
    }
    if (err.error === 'dry_run_stale' || err.error === 'dry_run_required') {
      this.lastDryRun.set(null); // force re-run
      this.snackBar.open('Re-run the dry-run check before executing.', 'Dismiss', {
        duration: 5000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    if (err.error === 'byte_count_mismatch') {
      this.lastDryRun.set(null);
      this.snackBar.open(
        'The dry-run result changed since you ran it — re-run and try again.',
        'Dismiss',
        { duration: 5000, panelClass: 'error-snackbar' },
      );
      return;
    }
    if (err.error === 'not_found') {
      this.snackBar.open(
        'The run record was not found — re-run from the status page.',
        'Dismiss',
        { duration: 6000, panelClass: 'error-snackbar' },
      );
      return;
    }
    this.snackBar.open(err.message ?? `Error: ${err.error}`, 'Dismiss', {
      duration: 5000,
      panelClass: 'error-snackbar',
    });
  }

  /** AM-13 — kill-switch OFF: render the global banner. */
  isMaintenanceDisabled(): boolean {
    return this.lastError()?.error === 'maintenance_disabled';
  }

  /** Track-by helpers for templates with `@for` loops. */
  trackByThreadId(_index: number, entry: CheckpointCleanupSkippedEntry): string {
    return entry.thread_id;
  }

  /** Convenience: typed access for the template's @if guards. */
  statusSnapshot(): CheckpointCleanupStatus | null {
    return this.status();
  }
}
