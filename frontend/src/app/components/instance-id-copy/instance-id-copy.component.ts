import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { Clipboard } from '@angular/cdk/clipboard';
import { MatSnackBar } from '@angular/material/snack-bar';

/**
 * ONE rendering surface for the click-to-copy instance-ID chip. Used by
 * ``app-instance-list`` (list row) and the chat header so the copy
 * behavior, toast wording, a11y semantics, and affordance styling cannot
 * drift between contexts.
 *
 * The chip renders a real <button> (native Enter/Space activation) whose
 * ``aria-label`` carries the FULL UUID so screen readers announce the
 * whole value even though only a truncated prefix is visible. The
 * truncated text itself is PROJECTED content — the call site owns how
 * short the prefix is (12 chars in the list, 8 in the chat header).
 *
 * Clipboard strategy (robust across contexts, no new deps):
 *   1. Prefer ``navigator.clipboard.writeText`` (secure contexts).
 *   2. Fall back to Angular CDK ``Clipboard`` — textarea +
 *      ``document.execCommand('copy')`` — for non-secure contexts
 *      (plain-HTTP LAN serving). CDK swallows internal errors and
 *      reports failure via its boolean return.
 * Success/failure is surfaced via the app-wide MatSnackBar toast —
 * never silent.
 */
@Component({
  selector: 'app-instance-id-copy',
  standalone: true,
  imports: [],
  templateUrl: './instance-id-copy.component.html',
  styleUrl: './instance-id-copy.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class InstanceIdCopyComponent {
  private readonly clipboard = inject(Clipboard);
  private readonly snackBar = inject(MatSnackBar);

  /** FULL instance UUID placed on the clipboard on click. */
  readonly instanceId = input.required<string>();

  protected async onCopyClick(event: Event): Promise<void> {
    // The chip usually lives INSIDE an interactive ancestor (the
    // instance-list row is an <a [routerLink]>). Swallow the event so
    // copying never navigates away — same pattern as the row's
    // pause/terminate buttons.
    event.preventDefault();
    event.stopPropagation();

    const id = this.instanceId();
    const copied = await this.writeToClipboard(id);
    if (copied) {
      this.snackBar.open(`Instance ${id} copied`, 'Close', {
        duration: 2000,
        panelClass: 'success-snackbar',
      });
    } else {
      this.snackBar.open('Failed to copy instance ID', 'Dismiss', {
        duration: 4000,
        panelClass: 'error-snackbar',
      });
    }
  }

  /**
   * Async Clipboard API first; CDK's execCommand fallback second.
   * Never throws — every failure path collapses to ``false``.
   */
  private async writeToClipboard(text: string): Promise<boolean> {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        // Permission denied / unfocused document / insecure context —
        // fall through to the CDK fallback.
      }
    }
    return this.clipboard.copy(text);
  }
}
