import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  MatDialogRef,
  MAT_DIALOG_DATA,
  MatDialogModule,
} from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../services/api.service';
import type { InstanceInfo } from '../../models';

export interface InstanceDeleteDialogData {
  instance: InstanceInfo;
}

/**
 * Two-step instance termination dialog.
 *
 * Step 1 (primary): offers three actions:
 *   * **Delete**  — terminate + permanent DB delete (requires a second
 *                   confirmation that *replaces* this dialog's content).
 *   * **Terminate** — existing terminate-only behaviour.
 *   * **Cancel** — dismiss the dialog without changes.
 *
 * Step 2 (only shown after Delete is chosen): a high-emphasis "Are you
 * sure?" prompt that performs the hard delete on confirm.
 *
 * The dialog performs the HTTP call itself (following the same pattern as
 * :class:`ProjectDeleteDialogComponent`) so the caller does not need to
 * know whether the request is soft or hard. Successful completion closes
 * the dialog and shows a snackbar; failures keep the dialog open so the
 * user can retry or cancel.
 */
@Component({
  selector: 'app-instance-delete-dialog',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    MatIconModule,
    MatSnackBarModule,
  ],
  templateUrl: './instance-delete-dialog.html',
  styleUrl: './instance-delete-dialog.scss',
})
export class InstanceDeleteDialogComponent {
  private readonly api = inject(ApiService);
  private readonly snackBar = inject(MatSnackBar);

  protected readonly dialogRef =
    inject<MatDialogRef<InstanceDeleteDialogComponent>>(MatDialogRef);
  protected readonly data =
    inject<InstanceDeleteDialogData>(MAT_DIALOG_DATA);

  /** Which view the dialog is currently rendering. */
  protected readonly view = signal<'primary' | 'confirm-delete'>('primary');

  /** True while the HTTP request is in flight. */
  protected readonly isBusy = signal(false);

  /** Display label for the instance — title when present, otherwise the short id. */
  protected readonly displayLabel = computed(() => {
    const title = this.data.instance.title?.trim();
    if (title) return title;
    const id = this.data.instance.instance_id;
    return id.length > 12 ? `${id.slice(0, 12)}...` : id;
  });

  protected handleCancel(): void {
    if (this.isBusy()) return;
    this.dialogRef.close(false);
  }

  /**
   * User chose "Terminate" on the primary view — perform a soft
   * terminate and close the dialog.
   */
  protected handleTerminate(): void {
    if (this.isBusy()) return;
    this.isBusy.set(true);

    this.api.deleteInstance(this.data.instance.instance_id, false).subscribe({
      next: () => {
        this.snackBar.open('Instance terminated', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.dialogRef.close({ action: 'terminate' });
      },
      error: (err) => {
        this.isBusy.set(false);
        const message = this.extractErrorMessage(err, 'Failed to terminate instance');
        this.snackBar.open(message, 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  /**
   * User chose "Delete" on the primary view — swap the dialog content for
   * the high-emphasis confirmation prompt. The primary view is *replaced*
   * (not stacked) as required by the spec.
   */
  protected handleChooseDelete(): void {
    if (this.isBusy()) return;
    this.view.set('confirm-delete');
  }

  /**
   * User backed out of the hard-delete confirmation. Return to the primary
   * view so they can pick Terminate / Cancel instead.
   */
  protected handleBack(): void {
    if (this.isBusy()) return;
    this.view.set('primary');
  }

  /**
   * User confirmed the hard delete on the secondary view.
   */
  protected handleConfirmDelete(): void {
    if (this.isBusy()) return;
    this.isBusy.set(true);

    this.api.deleteInstance(this.data.instance.instance_id, true).subscribe({
      next: () => {
        this.snackBar.open('Instance deleted', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.dialogRef.close({ action: 'delete' });
      },
      error: (err) => {
        this.isBusy.set(false);
        const message = this.extractErrorMessage(err, 'Failed to delete instance');
        this.snackBar.open(message, 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  private extractErrorMessage(err: unknown, fallback: string): string {
    const candidate = err as { error?: { detail?: string }; message?: string };
    return (
      candidate?.error?.detail ||
      candidate?.message ||
      fallback
    );
  }
}