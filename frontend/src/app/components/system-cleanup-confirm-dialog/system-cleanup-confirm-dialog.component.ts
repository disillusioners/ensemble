import { Component, inject, signal } from '@angular/core';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import {
  CleanupPreflight,
  cleanupDeferNote,
  CLEANUP_TRUTH_SPLIT_COPY,
  CLEANUP_TRUTH_SURVIVOR_NOTE,
} from '../../models/cleanup-preflight.model';

/**
 * Dialog data payload for the System Cleanup confirmation.
 *
 * The dialog receives the preflight response as a partial payload because
 * all fields are optional at this boundary for compatibility with older
 * daemon responses.
 */
export type SystemCleanupConfirmData = Partial<CleanupPreflight>;

/**
 * Confirmation dialog shown before issuing a ``POST /api/jobs/cleanup``
 * request from the Jobs page.
 *
 * WS4 reframe: the operation cancels all jobs and cleans up stalled
 * missions, and is confirmed TWICE (stage 1 summary → stage 2 final
 * confirmation). Stage 1's primary button arms the dialog; ONLY Cancel
 * (dialog close with ``false``) disarms.
 *
 * Returns:
 *   * ``true``  — operator double-confirmed; the page will proceed
 *                 with the cleanup request.
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
      .bad-state-warning,
      .zombie-warning {
        color: #f43f5e;
        margin-top: 12px;
        font-weight: 500;
      }
      .will-remain {
        color: #38bdf8;
        margin-top: 12px;
      }
      .defer-note {
        color: #f59e0b;
        margin-top: 12px;
      }
      .final-confirm {
        margin-top: 16px;
        padding: 12px;
        border: 1px solid #f43f5e;
        border-radius: 6px;
        background: rgba(244, 63, 94, 0.08);
      }
      .final-confirm p {
        margin: 0 0 8px 0;
        font-weight: 600;
        color: #f43f5e;
      }
      .live-id-list {
        color: #94a3b8;
        font-size: 12px;
        word-break: break-all;
      }
    `,
  ],
  template: `
    <h2 mat-dialog-title>System Cleanup</h2>
    <div mat-dialog-content>
      <p>
        This will <strong>cancel ALL jobs (every lane)</strong> AND
        <strong>clean up stalled missions</strong> across ALL projects.
        This action cannot be undone.
      </p>
      @if (data.bad_state_count && data.bad_state_count > 0) {
        <p class="bad-state-warning">
          ⚠ {{ data.bad_state_count }} bad-state tasks will be reconciled.
        </p>
      }
      @if (data.zombie_instance_count && data.zombie_instance_count > 0) {
        <p class="zombie-warning">
          ⚠ {{ data.zombie_instance_count }}
          {{ data.zombie_instance_count === 1 ? 'stalled mission' : 'stalled missions' }}
          will be reaped (terminated).
        </p>
      }
      <p class="will-remain">
        {{ truthSplitCopy }}
        @if (liveIds().length > 0) {
          <span class="live-id-list">{{ liveIds().join(', ') }}</span>
        }
      </p>
      @if (liveIds().length > 0) {
        <p class="survivor-note">
          {{ survivorNote }}
        </p>
      }
      @if (deferCount() > 0) {
        <p class="defer-note">
          {{ deferCount() }} deferred
          {{ deferCount() === 1 ? 'message' : 'messages' }} waiting on the defer
          lane.
          @if (deferNote(); as note) {
            {{ note }}
          }
        </p>
      }
      @if (armed()) {
        <div class="final-confirm">
          <p>Final confirmation</p>
          <p style="font-weight: 400; color: inherit;">
            Cancel ALL jobs and clean up stalled missions? This cannot be
            undone.
          </p>
        </div>
      }
    </div>
    <div mat-dialog-actions align="end">
      <button mat-button (click)="dialogRef.close(false)">Cancel</button>
      @if (!armed()) {
        <button mat-raised-button color="warn" (click)="arm()">Continue</button>
      } @else {
        <button mat-raised-button color="warn" (click)="dialogRef.close(true)">
          Cleanup — final confirm
        </button>
      }
    </div>
  `,
})
export class SystemCleanupConfirmDialogComponent {
  readonly dialogRef = inject<MatDialogRef<SystemCleanupConfirmDialogComponent>>(MatDialogRef);
  readonly data = inject<SystemCleanupConfirmData>(MAT_DIALOG_DATA);

  /** Double-confirm stage: false = stage 1, true = armed (stage 2). */
  readonly armed = signal(false);

  arm(): void {
    // Stage 1 → stage 2. The ONLY disarm path is Cancel (dialog
    // close with ``false``) — the armed state never self-reverts.
    this.armed.set(true);
  }

  readonly liveIds = () => this.data.live_instance_ids ?? [];
  readonly deferCount = () => this.data.defer_blocked_count ?? 0;
  readonly deferNote = () => cleanupDeferNote(this.data.defer_holder_kind);
  /** Truth-split copy VERBATIM match against
   *  ``CLEANUP_TRUTH_SPLIT_COPY`` — unblock-round ITEM 5 pin. */
  readonly truthSplitCopy = CLEANUP_TRUTH_SPLIT_COPY;
  /** Truth-survivor caption VERBATIM match against
   *  ``CLEANUP_TRUTH_SURVIVOR_NOTE`` — rendered in the template
   *  (gated on ``liveIds().length > 0``). Cap-exception micro-round
   *  ITEM 1, 2026-09-06 — keeps the caption's render path pinned via
   *  ``tests/unit/test_fe_dialog_survivor_render_path.py`` (import
   *  line + readonly exposure + template interpolation). */
  readonly survivorNote = CLEANUP_TRUTH_SURVIVOR_NOTE;
}
