import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
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
import { SseService } from '../../services/sse.service';
import { InstancesViewStateService } from '../../services/instances-view-state.service';
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
  private readonly sseService = inject(SseService);
  private readonly router = inject(Router);
  /**
   * View-state singleton for the root-mounted detail overlay.
   * Successful termination AND hard-delete from this dialog must drop
   * the cached id (W1) so the next "Instances" nav-link click does
   * not restore a dead instance. The service is a no-op when the
   * terminated id doesn't match the current cache.
   */
  private readonly viewState = inject(InstancesViewStateService);

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

    const terminatedId = this.data.instance.instance_id;
    this.api.deleteInstance(terminatedId, false).subscribe({
      next: () => {
        // W1: drop the cached id from the view-state service. The
        // service is a no-op when the terminated id isn't the active
        // cache, so this is safe for unrelated rows too.
        this.viewState.clearInstance(terminatedId);
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
   *
   * After a successful hard delete we must stop the SSE channel for the now-
   * gone instance and close any chat tab the user had open against it.
   * This mirrors the chat page's own cleanup on termination
   * (``chat.component.ts:onTerminateInstance`` + ``ngOnDestroy``): the SSE
   * channel is closed unconditionally so a stale connection can never
   * resurrect the deleted row, and if the deleted instance is currently the
   * active chat route we navigate to ``/`` so the user is bounced back to
   * home instead of landing on a now-empty chat view.
   */
  protected handleConfirmDelete(): void {
    if (this.isBusy()) return;
    this.isBusy.set(true);

    const deletedInstanceId = this.data.instance.instance_id;
    this.api.deleteInstance(deletedInstanceId, true).subscribe({
      next: () => {
        // W1: drop the cached id from the view-state service. Same
        // semantics as handleTerminate — clearInstance is a no-op
        // when the id isn't the active cache.
        this.viewState.clearInstance(deletedInstanceId);
        this.snackBar.open('Instance deleted', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });

        // Disconnect the SSE channel before closing the dialog so we stop
        // receiving events for an instance that no longer exists in the DB.
        // chat.component.ts does the same in its ngOnDestroy / handleInstanceIdChange
        // paths; the call is idempotent and safe when no channel is open.
        this.sseService.disconnect();
        this.sseService.clearEvents();

        // Close any chat tab the user had open for this instance. The chat
        // page URL is ``/projects/:projectContext/instances/:instanceId`` —
        // matching that segment is the same signal chat.component.ts uses
        // (its ``currentInstanceId() === instanceId`` check) because the URL
        // param and the component state are kept in sync by handleInstanceIdChange.
        if (this.router.url.includes(`/instances/${deletedInstanceId}`)) {
          this.router.navigate(['/']);
        }

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
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    if (typeof detail === 'string') return detail;
    // FastAPI may return `detail` as an object like `{code, message}` for
    // structured error responses — surface the message instead of the
    // `[object Object]` placeholder.
    if (typeof detail === 'object' && detail && 'message' in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === 'string') return message;
    }
    const candidate = err as { message?: string };
    return candidate?.message || fallback;
  }
}