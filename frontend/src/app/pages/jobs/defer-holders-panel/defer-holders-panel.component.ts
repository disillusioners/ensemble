import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import {
  DeferBlockHolder,
  DeferBlockedStatus,
  deferPageBanner,
  orderDeferHolders,
} from '../../../models/defer-blocked.model';

/**
 * P4 — Page-scoped inline holders panel under the page banner.
 *
 * The page banner (above the filter bar) surfaces the severity of a
 * defer-blocked state; the holders panel is the ONE-CLICK DRILL-DOWN
 * (P4 task 2: "defer-blocked visible ≤1 interaction from page root").
 *
 * This component is purely PRESENTATIONAL — it carries no fetch
 * state, opens no service, and fires no destructive action on its
 * own. The parent page owns:
 *   * the fetcher wiring (JobsPageStore.fetchDeferBlocked)
 *   * the two-stage confirm dialog (the page opens ConfirmDialog with
 *     the holder's identity + irreversibility copy and only fires the
 *     service call after ``afterClosed`` returns ``true``)
 *   * the post-action refresh (the page calls ``fetchDeferBlocked``
 *     again after a successful action so the panel re-renders
 *     without the resolved holder)
 *
 * The component is responsible for:
 *   * visibility (hidden when no banner, hidden when banner is
 *     anomaly-only — anomaly has no holders to drill into)
 *   * holder ordering (paused > stalled > live, by way of
 *     ``orderDeferHolders``)
 *   * per-holder row rendering (instance, agent, kind chip, since)
 *   * per-holder action buttons (``forceComplete`` is disabled for
 *     paused holders — the WS4 server gate; the page's confirm
 *     dialog is the UX gate, but disabling here prevents the
 *     no-action path from being one-click reachable)
 *   * degraded-note rendering when the parent's last fetch failed
 *
 * Cross-seam invariant: holder list shape matches
 * ``deferPageBanner(status).holders`` — the panel consumes the SAME
 * ordering the banner does, so a banner payload and a panel payload
 * cannot disagree on whose row is where.
 */
@Component({
  selector: 'app-defer-holders-panel',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule],
  templateUrl: './defer-holders-panel.component.html',
  styleUrl: './defer-holders-panel.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DeferHoldersPanelComponent {
  /**
   * Current defer-blocked payload from the page store. The panel
   * renders ONLY when this is non-null AND carries holders (the
   * RED-anomaly state has no holders to drill into — the banner
   * alone owns that affordance, with its "Open System Cleanup"
   * button).
   */
  readonly status = input<DeferBlockedStatus | null>(null);

  /**
   * True when the parent's LAST fetch failed and the visible payload
   * is the retained one (P4 task 4: replaces the silent swallow at
   * the legacy `:587-589` path). The banner surfaces "last check
   * failed — showing retained state" via the banner's degraded flag;
   * the panel re-uses the same flag for its own inline note so the
   * two stay coherent.
   */
  readonly degraded = input<boolean>(false);

  /**
   * Disable the action buttons while an action is in flight. The
   * parent flips this when the service call is on the wire and
   * resets it on success / error. Belt-and-braces with the
   * ConfirmDialog — even if the dialog close races, the disabled
   * buttons prevent a double-fire.
   */
  readonly actionInFlight = input<boolean>(false);

  /**
   * Stage-1 of the two-stage confirm: operator clicked "Force
   * complete" on the holder row. The page wires this to open a
   * ConfirmDialog (naming the holder + stating irreversibility).
   * The actual destructive call fires ONLY after the dialog
   * resolves ``true``.
   */
  readonly forceComplete = output<DeferBlockHolder>();

  /**
   * Stage-1 of the two-stage confirm: operator clicked "Resend
   * foreground" on the holder row. Same confirm-flow as
   * ``forceComplete``.
   */
  readonly resendForeground = output<DeferBlockHolder>();

  /**
   * Visible iff the parent opened the panel (``status`` non-null)
   * AND the banner carries holders (the anomaly path is
   * banner-only). Pinned helper so the page template can drive the
   * ``@if`` directly off the component's open state.
   */
  protected readonly isOpen = computed<boolean>(() => {
    const banner = deferPageBanner(this.status());
    if (banner === null) {
      return false;
    }
    return banner.holders.length > 0;
  });

  /**
   * The ordered holder list — paused > stalled > live (delegates to
   * the same helper the banner uses so the two stay coherent).
   */
  protected readonly orderedHolders = computed<DeferBlockHolder[]>(() => {
    const banner = deferPageBanner(this.status());
    if (banner === null) {
      return [];
    }
    return orderDeferHolders(banner.holders);
  });

  /**
   * The animate copy for the "Resume or terminate to unblock"
   * paused-only row — does NOT render for stalled/live because the
   * recovery path differs (pause ⇒ resume/terminate the holder;
   * stalled ⇒ force-complete; live ⇒ no action).
   */
  protected resumeHint(holder: DeferBlockHolder): string | null {
    if (holder.kind === 'paused') {
      return 'Resume or terminate the holder instance to unblock.';
    }
    return null;
  }

  /**
   * Force-complete button is OFFERED ONLY for stalled holders. Live
   * holders need no action; paused holders need resume/terminate
   * (the WS4 server re-derives mirrors-only at execution time, but
   * the FE gate prevents the no-op click). This disabled state is
   * the visible "why is this greyed out" affordance for the
   * operator — the same truth the tooltip on the indicator badge
   * carries.
   */
  protected isForceCompleteEnabled(holder: DeferBlockHolder): boolean {
    return holder.kind === 'stalled';
  }

  /**
   * Resend-foreground is OFFERED for stalled AND live holders — the
   * re-send is a safe foreground move on either. Paused holders
   * cannot re-send (the holder is not consuming), so the button is
   * disabled for them.
   */
  protected isResendEnabled(holder: DeferBlockHolder): boolean {
    return holder.kind !== 'paused';
  }

  /**
   * Template-friendly format helper — the model exposes the pure
   * function and the template stays thin.
   */
  protected formatSince(since: string | null): string {
    if (!since) {
      return 'unknown time';
    }
    return `${since.replace('T', ' ').slice(0, 16)} UTC`;
  }
}
