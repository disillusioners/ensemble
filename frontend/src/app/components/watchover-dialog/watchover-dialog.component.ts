import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  MAT_DIALOG_DATA,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

/**
 * Data payload for the Watchover activation dialog.
 *
 * - ``instanceId`` — required: the instance the user is enabling watchover on.
 * - ``instanceName`` — optional: a human-readable label shown beneath the
 *   title so the operator knows exactly which instance the dialog applies to
 *   (matches the chat header's name display). Falls back to the short
 *   instance id when omitted.
 */
export interface WatchoverDialogData {
  instanceId: string;
  instanceName?: string | null;
}

/**
 * Result payload returned to the caller via ``MatDialogRef.close()``.
 *
 * - ``nextCommand`` — required (already guarded by ``canEnable()``). The
 *   command the agent should execute after watchover is enabled. The
 *   backend uses this as a fresh message injected into the resumed
 *   graph turn.
 * - ``watchoverRequirement`` — optional constraints for the watcher
 *   (e.g. "read-only operations only"). ``null`` when the user leaves
 *   the field empty so the backend applies its default context builder.
 */
export interface WatchoverDialogResult {
  nextCommand: string;
  watchoverRequirement: string | null;
}

/**
 * Watchover activation dialog.
 *
 * Replaces the legacy ``window.prompt()`` flow in ``ChatComponent`` so
 * the operator can both:
 *   1. Specify the next command to run after watchover is armed
 *      (the backend forwards this as the resume message), and
 *   2. Optionally constrain the watcher's guardrails with a free-form
 *      requirement description.
 *
 * The dialog is only shown when the instance is NOT in a running state
 * (``running`` / ``active``) — that case is handled by the chat header
 * directly with no dialog. See ``ChatComponent.onToggleWatchover``.
 *
 * Visual style: the caller supplies ``panelClass: 'watchover-dialog-panel'``
 * (and the global ``dark-modal-panel`` is added by the project-wide
 * backdrop hook). The SCSS below only handles internal spacing so the
 * dialog stays consistent with the rest of the destructive-action
 * flows in the app.
 *
 * Returns:
 *   * ``WatchoverDialogResult`` — the user clicked "Enable Watchover"
 *     with valid input.
 *   * ``null`` — the user clicked Cancel or dismissed the dialog via
 *     backdrop / Esc. The caller treats this as a no-op.
 */
@Component({
  selector: 'app-watchover-dialog',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
  ],
  templateUrl: './watchover-dialog.component.html',
  styleUrl: './watchover-dialog.component.scss',
})
export class WatchoverDialogComponent {
  protected readonly dialogRef = inject<MatDialogRef<WatchoverDialogComponent, WatchoverDialogResult | null>>(
    MatDialogRef,
  );
  protected readonly data = inject<WatchoverDialogData>(MAT_DIALOG_DATA);

  /**
   * The next command the agent should execute after watchover is enabled.
   * Required — the submit button is disabled until this signal holds a
   * non-whitespace value (see ``canEnable``).
   */
  readonly nextCommand = signal('');

  /**
   * Optional free-form constraint for the watcher (e.g. "read-only
   * operations only"). Empty string when the user did not enter
   * anything; resolved to ``null`` on submit so the backend applies
   * its default heuristic.
   */
  readonly watchoverRequirement = signal('');

  /**
   * True when the dialog has enough input to enable the submit button.
   * Trimmed so the user cannot game the validation with whitespace.
   */
  readonly canEnable = computed(() => this.nextCommand().trim().length > 0);

  /**
   * Submit — close the dialog with the captured payload. Mirrors the
   * ``confirm-dialog`` pattern (close with a typed result, no side
   * effects performed here).
   */
  protected onEnable(): void {
    if (!this.canEnable()) return;
    const result: WatchoverDialogResult = {
      nextCommand: this.nextCommand().trim(),
      watchoverRequirement: this.watchoverRequirement().trim() || null,
    };
    this.dialogRef.close(result);
  }

  /**
   * Cancel — close the dialog with ``null``. Callers must treat
   * ``null`` as a no-op (matches the backdrop-dismiss contract across
   * the rest of the project's dialog flows).
   */
  protected onCancel(): void {
    this.dialogRef.close(null);
  }
}
