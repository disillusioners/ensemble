import { Component, inject } from '@angular/core';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';

/**
 * Dialog data payload for the System Cleanup confirmation.
 *
 * Currently empty — the dialog renders a fixed copy that does not
 * depend on any caller-supplied counts. Kept as an exported
 * interface so future enhancements (e.g. preflight counts from the
 * backend) can extend the shape without breaking call sites.
 */
export interface SystemCleanupConfirmData {
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
  template: `
    <h2 mat-dialog-title>System Cleanup</h2>
    <div mat-dialog-content>
      <p>This will cancel ALL pending and running jobs across ALL projects. This action cannot be undone. Continue?</p>
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