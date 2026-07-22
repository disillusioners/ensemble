import { Component, computed, inject } from '@angular/core';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';

/**
 * Data payload for the reusable confirmation dialog.
 *
 * All fields are optional — sensible defaults are applied via the
 * ``computed`` signals below so callers can open the dialog with a
 * single line of copy when the destructive action is generic.
 *
 * - ``title`` — dialog title (defaults to ``"Confirm"``).
 * - ``message`` — body text shown to the user (defaults to
 *   ``"Are you sure you want to proceed?"``).
 * - ``confirmLabel`` — text on the confirm button (defaults to
 *   ``"Confirm"``).
 * - ``cancelLabel`` — text on the dismiss button (defaults to
 *   ``"Cancel"``).
 * - ``destructive`` — when ``true`` the confirm button is rendered
 *   with ``color="warn"`` to match the rest of the destructive-action
 *   flows in the app (e.g. Delete Project, System Cleanup).
 */
export interface ConfirmDialogData {
  title?: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

/**
 * Reusable confirmation dialog used by destructive actions across the
 * app. The dialog is intentionally minimal — it does NOT perform the
 * action itself; it only returns ``true`` on confirm and ``false``
 * (or ``undefined`` on backdrop dismiss) on cancel. The caller wires
 * the actual side-effect after the dialog closes.
 *
 * Visual style follows the project-wide dark theme:
 *   * ``panelClass: 'dark-modal-panel'`` is applied by the caller so
 *     the rest of the styling (mat-mdc-dialog-container color) is
 *     handled by the existing global hooks.
 *   * The dialog's own ``confirm-dialog`` CSS class scopes the
 *     component-specific spacing / copy rules below.
 *
 * Returns:
 *   * ``true``  — user clicked the confirm button.
 *   * ``false`` — user clicked the cancel button.
 *   * ``undefined`` — user dismissed the dialog via backdrop / Esc.
 *     Callers should treat ``undefined`` the same as ``false`` (no
 *     destructive action).
 */
@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [MatDialogModule, MatButtonModule, MatIconModule],
  templateUrl: './confirm-dialog.component.html',
  styleUrl: './confirm-dialog.component.scss',
})
export class ConfirmDialogComponent {
  protected readonly dialogRef = inject<MatDialogRef<ConfirmDialogComponent, boolean>>(
    MatDialogRef,
  );
  protected readonly data = inject<ConfirmDialogData>(MAT_DIALOG_DATA, { optional: true });

  /**
   * Resolved title. Falls back to ``"Confirm"`` when the caller does
   * not supply one so the dialog never renders an empty title bar.
   * Public (no modifier) so the template can read it.
   */
  protected readonly title = computed(() => this.data?.title?.trim() || 'Confirm');

  /**
   * Resolved body message. Falls back to a generic confirmation copy
   * so the dialog surfaces SOMETHING even for callers that pass no
   * data at all.
   */
  protected readonly message = computed(
    () => this.data?.message?.trim() || 'Are you sure you want to proceed?',
  );

  /**
   * Resolved confirm button label. Defaults to ``"Confirm"``.
   */
  protected readonly confirmLabel = computed(() => this.data?.confirmLabel?.trim() || 'Confirm');

  /**
   * Resolved cancel button label. Defaults to ``"Cancel"``.
   */
  protected readonly cancelLabel = computed(() => this.data?.cancelLabel?.trim() || 'Cancel');

  /**
   * Whether the confirm button should render with the destructive
   * (``color="warn"``) styling. Defaults to ``true`` so the dialog
   * errs on the side of caution — a confirmation dialog is almost
   * always shown for a destructive action, and the visual emphasis
   * matches the rest of the project's destructive-action flows.
   */
  protected readonly destructive = computed(() => this.data?.destructive ?? true);

  /**
   * Cancel / dismiss — close the dialog with ``false``. Mirrors the
   * pattern used by the other confirmation dialogs in the project
   * (SystemCleanupConfirmDialogComponent, ProjectDeleteDialogComponent).
   */
  protected onCancel(): void {
    this.dialogRef.close(false);
  }

  /**
   * Confirm — close the dialog with ``true``. The caller is
   * responsible for performing the actual destructive action after
   * observing this result via ``afterClosed()``.
   */
  protected onConfirm(): void {
    this.dialogRef.close(true);
  }
}
