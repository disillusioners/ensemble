import { Component, inject, signal } from '@angular/core';
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
 *
 * Phase 5 — ``zombie_instance_count`` carries the parallel count of
 * non-terminal instances with no live work that the cleanup is about
 * to terminate. Both counts default to undefined and the dialog
 * renders a warning block only when the value is > 0.
 *
 * WS4 (2026-09-06) — the preflight carries the full live-vs-reap
 * split: ``live_instance_count`` / ``live_instance_ids`` list the
 * non-terminal instances cleanup will NOT touch ("will remain"),
 * and ``defer_blocked_count`` surfaces the defer lane SEPARATELY
 * from bad-state (they are different concepts with different
 * remediations — cleanup does NOT cancel deferred messages; the
 * defer warning's holder actions do). All fields optional for
 * backward compatibility.
 */
export interface SystemCleanupConfirmData {
  bad_state_count?: number;
  zombie_instance_count?: number;
  live_instance_count?: number;
  live_instance_ids?: string[];
  defer_blocked_count?: number;
  // Reserved for future use (e.g. pending counts passed in by caller).
}

/**
 * Confirmation dialog shown before issuing a ``POST /api/jobs/cleanup``
 * request from the Jobs page.
 *
 * WS4 reframe: the operation is "Cancel ALL jobs (every lane) + reap
 * idle/stalled missions" — destructive and confirmed TWICE (stage 1
 * summary → stage 2 final confirmation). Stage 1's primary button
 * arms the dialog; ONLY Cancel (dialog close with ``false``) disarms.
 * The transition is a constant set on the ``armed`` signal — no
 * helper extraction is warranted (nothing to logic-mirror).
 *
 * The dialog states the by-design scope boundary explicitly: deferred
 * messages are NOT cancelled here (mirror protection) — the defer
 * warning's holder actions are the unstick path.
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
        <strong>reap idle/stalled missions</strong> across ALL projects.
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
          {{ data.zombie_instance_count === 1 ? 'idle/stalled mission' : 'idle/stalled missions' }}
          will be reaped (terminated).
        </p>
      }
      @if (liveCount() > 0) {
        <p class="will-remain">
          {{ liveCount() }}
          {{ liveCount() === 1 ? 'live mission' : 'live missions' }} will remain —
          running tasks, active jobs and live children are never terminated by
          cleanup.
          @if (liveIds().length > 0) {
            <span class="live-id-list">{{ liveIds().join(', ') }}</span>
          }
        </p>
      }
      <p class="defer-note">
        @if (deferCount() > 0) {
          {{ deferCount() }} deferred
          {{ deferCount() === 1 ? 'message' : 'messages' }} waiting on the defer
          lane:
        }
        deferred messages are not cancelled here — use the defer warning's
        actions (force-complete / re-send as foreground) instead.
      </p>
      @if (armed()) {
        <div class="final-confirm">
          <p>Final confirmation</p>
          <p style="font-weight: 400; color: inherit;">
            Cancel ALL jobs and reap ALL idle/stalled missions? This cannot be
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

  readonly liveCount = () => this.data.live_instance_count ?? 0;
  readonly liveIds = () => this.data.live_instance_ids ?? [];
  readonly deferCount = () => this.data.defer_blocked_count ?? 0;
}
