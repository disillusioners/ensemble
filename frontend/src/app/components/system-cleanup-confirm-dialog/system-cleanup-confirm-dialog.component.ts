import { Component, inject } from '@angular/core';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';

/**
 * Dialog data payload for the System Cleanup confirmation.
 *
 * Phase 4 — the dialog now accepts the preflight bad-state count so
 * the operator sees a contextual warning before committing to a
 * full system cleanup. ``bad_state_count`` is optional for backward
 * compatibility with any existing call site that opens the dialog
 * without a payload.
 */
export interface SystemCleanupConfirmData {
  bad_state_count?: number;
  // Reserved for future use (e.g. pending counts passed in by caller).
}

/**
 * Confirmation dialog shown before issuing a ``POST /api/jobs/cleanup``
 * request from the Jobs page.
 *
 * Returns:
 *   * ``true``  — operator confirmed; the page will proceed with the
 *                 cleanup request.
 *   * ``false`` — operator cancelled or dismissed the dialog.
 *
 * Follows the same inline-confirmation pattern as ``SwitchConfirmDialog``
 * in ``migration.component.ts`` so the dialog feels consistent with
 * the rest of the app's destructive-action flow.
 */
@Component({
  selector: 'app-system-cleanup-confirm-dialog',
  standalone: true,
  imports: [MatDialogModule, MatButtonModule],
  styles: [
    `
      .bad-state-warning {
        color: #f43f5e;
        margin-top: 12px;
        font-weight: 500;
      }
    `,
  ],
  template: `
    <h2 mat-dialog-title>System Cleanup</h2>
    <div mat-dialog-content>
      <p>This will cancel ALL pending and running jobs across ALL projects. This action cannot be undone. Continue?</p>
      @if (data.bad_state_count && data.bad_state_count > 0) {
        <p class="bad-state-warning">
          ⚠ {{ data.bad_state_count }} bad-state tasks will be reconciled.
        </p>
      }
    </div>
    <div mat-dialog-actions align="end">
      <button mat-button (click)="dialogRef.close(false)">Cancel</button>
      <button mat-raised-button color="warn" (click)="dialogRef.close(true)">Cleanup</button>
    </div>
  `,
})
export class SystemCleanupConfirmDialogComponent {
  readonly dialogRef = inject<MatDialogRef<SystemCleanupConfirmDialogComponent>>(MatDialogRef);
  readonly data = inject<SystemCleanupConfirmData>(MAT_DIALOG_DATA);
}
