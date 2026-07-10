import { Component, input, output, signal, inject, TemplateRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  Overlay,
  OverlayModule,
  CdkOverlayOrigin,
  ConnectedOverlayPositionChange,
  ConnectedPosition,
} from '@angular/cdk/overlay';

/**
 * A "real" popup for the graph-mode todo nodes: a CDK connected overlay that
 * anchors to a trigger element (the node's comment / sub-task button) and
 * floats in the global overlay pane — so it is NOT clipped by the todo
 * component's `overflow: hidden`, unlike a plain absolutely-positioned div.
 *
 * The body is supplied as a `TemplateRef` (`content` input) so the caller
 * keeps full control of the popup's inner markup and bindings (the template
 * is declared in the caller's view and therefore resolves against the
 * caller's component context when stamped here via `ngTemplateOutlet`).
 *
 * CDK's `FlexibleConnectedPositionStrategy` (driven by `positions`) picks
 * the first fitting placement and flips automatically — no manual edge-flip
 * math. The chosen placement is exposed as the `placement-*` class on the
 * shell so the arrow notch can point the right way.
 */
@Component({
  selector: 'app-todo-graph-popup',
  standalone: true,
  imports: [CommonModule, OverlayModule],
  templateUrl: './todo-graph-popup.component.html',
  styleUrl: './todo-graph-popup.component.scss',
})
export class TodoGraphPopupComponent {
  private readonly overlay = inject(Overlay);

  /** Closes the overlay when the user scrolls — the standard CDK behaviour. */
  readonly scrollStrategy = this.overlay.scrollStrategies.close();

  /** The trigger element (a button carrying `cdkOverlayOrigin`). */
  readonly origin = input<CdkOverlayOrigin | null>(null);

  /** Whether the overlay is open. Guarded by `origin` so CDK never attaches
   *  without a valid anchor. */
  readonly open = input(false);

  /** The popup body, declared as an `<ng-template>` in the caller. */
  readonly content = input<TemplateRef<unknown> | null>(null);

  /** Emitted when the overlay detaches (scroll-close, outside-origin detach).
   *  The caller uses this to reset its open state. */
  readonly closed = output<void>();

  /** Current placement, driven by CDK's position-change event so the arrow
   *  notch points toward the node. */
  readonly placement = signal<'right' | 'left' | 'above' | 'below'>('right');

  // Prefer right of the node, then left, then below, then above. CDK keeps
  // the first that fits the viewport (with the viewport margin below), so
  // the edge-flip is automatic.
  readonly positions: ConnectedPosition[] = [
    { originX: 'end', originY: 'center', overlayX: 'start', overlayY: 'center', panelClass: 'placement-right' },
    { originX: 'start', originY: 'center', overlayX: 'end', overlayY: 'center', panelClass: 'placement-left' },
    { originX: 'center', originY: 'bottom', overlayX: 'center', overlayY: 'top', panelClass: 'placement-below' },
    { originX: 'center', originY: 'top', overlayX: 'center', overlayY: 'bottom', panelClass: 'placement-above' },
  ];

  onPositionChange(change: ConnectedOverlayPositionChange): void {
    const cls = change.connectionPair.panelClass as string;
    if (cls === 'placement-left') this.placement.set('left');
    else if (cls === 'placement-below') this.placement.set('below');
    else if (cls === 'placement-above') this.placement.set('above');
    else this.placement.set('right');
  }

  onDetach(): void {
    this.closed.emit();
  }
}
