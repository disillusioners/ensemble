import { Injectable, inject, OnDestroy, DOCUMENT } from '@angular/core';
import { Overlay, OverlayRef } from '@angular/cdk/overlay';
import { ComponentPortal } from '@angular/cdk/portal';
import { MatDialog } from '@angular/material/dialog';
import { Subscription } from 'rxjs';
import {
  MermaidActionsMenuComponent,
  MermaidMenuAction,
} from '../components/mermaid-actions-menu/mermaid-actions-menu.component';
import {
  MermaidFullscreenDialogComponent,
  MermaidFullscreenDialogData,
} from '../components/mermaid-fullscreen-dialog/mermaid-fullscreen-dialog.component';

/**
 * Result returned by the service when a clipboard write succeeds.
 * The chat-interface shows a small inline status pill using this so
 * the user gets feedback that the action actually fired.
 */
export interface MermaidCopyResult {
  success: boolean;
  action: MermaidMenuAction | 'fullscreen' | 'image-fullscreen';
  message: string;
}

/**
 * Coordinates all user-facing interactions with a rendered Mermaid
 * chart: opening the copy menu, opening the fullscreen dialog, and
 * performing the underlying clipboard writes.
 *
 * The service is `providedIn: 'root'` because it owns no per-component
 * state; it just brokers calls between the chart overlay buttons
 * (which live in the markdown-rendered DOM) and the Angular Material
 * dialog / CDK overlay infrastructure.
 */
@Injectable({ providedIn: 'root' })
export class MermaidActionsService implements OnDestroy {
  private readonly overlay = inject(Overlay);
  private readonly dialog = inject(MatDialog);
  private readonly document = inject(DOCUMENT);

  /** Track open overlays so we can dispose them on teardown. */
  private readonly openOverlays = new Set<OverlayRef>();

  /**
   * Track the currently-open copy-menu overlay (if any). Mermaid
   * buttons are injected as raw DOM, so there is no Angular component
   * lifecycle to enforce "only one menu at a time" — without this
   * tracker, rapid clicks on different chart buttons could stack two
   * CDK overlays (and their backdrops) on top of each other.
   *
   * `activeMenuOnDismiss` is the caller's dismiss callback captured
   * at open time so we can fire it when a manually-disposed menu
   * (e.g. replaced by a newer one or torn down by `closeAll`) lets
   * the caller reset its "menu open" sentinel state.
   */
  private activeMenuOverlay: OverlayRef | null = null;
  private activeMenuOnDismiss: (() => void) | null = null;

  ngOnDestroy(): void {
    this.closeAll();
  }

  /**
   * Dispose every open CDK overlay (copy menus) and clear our
   * tracking sets. Called from the owning component's `ngOnDestroy`
   * so we tear down menu UI before the host view goes away, and
   * also defensively callable on demand (e.g. before navigating
   * between chat instances) to make sure no orphaned backdrop is
   * left behind.
   */
  closeAll(): void {
    // Dispose the active menu first so its `onDismiss` callback fires
    // (resetting the chat-interface sentinel) before the bulk teardown.
    this.disposeActiveMenu(true);
    for (const ref of this.openOverlays) {
      ref.dispose();
    }
    this.openOverlays.clear();
    this.activeMenuOverlay = null;
    this.activeMenuOnDismiss = null;
  }

  /**
   * Tear down the currently-open menu (if any). When `callDismiss`
   * is true the caller's `onDismiss` callback is invoked so it can
   * reset its "menu open" sentinel — this matters when replacing
   * an open menu with a new one (the sentinel must move to the
   * new chart) and during `closeAll()`.
   */
  private disposeActiveMenu(callDismiss: boolean): void {
    const existing = this.activeMenuOverlay;
    const dismiss = this.activeMenuOnDismiss;
    this.activeMenuOverlay = null;
    this.activeMenuOnDismiss = null;
    if (existing) {
      // `dispose()` triggers the overlay's `detachments()` observable,
      // which is what removes it from `openOverlays`. Calling dispose
      // twice is a no-op in CDK, so this is safe even if the
      // detachment also fires from a `settle()` path.
      existing.dispose();
    }
    if (callDismiss && dismiss) {
      try {
        dismiss();
      } catch {
        // Swallow: callers' dismiss handlers are best-effort UI resets.
      }
    }
  }

  /**
   * Show the two-item copy menu anchored to the trigger button.
   *
   * The menu is hosted inside a CDK `Overlay` so it can float above
   * the scrollable messages container without needing to be part of
   * the Angular view tree (the trigger buttons are not Angular
   * components — they are raw DOM elements injected into the
   * markdown-rendered `.mermaid` block).
   *
   * `onResult` is invoked whenever a menu item is picked so the
   * caller can show a transient status pill on the chart.
   * `onDismiss` fires when the menu closes without a selection
   * (backdrop click, ESC, etc.) so the caller can clear any "menu
   * open" UI state.
   */
  openCopyMenu(
    triggerEl: HTMLElement,
    context: { svg: string; source: string; title?: string },
    onResult: (result: MermaidCopyResult) => void,
    onDismiss?: () => void,
  ): void {
    if (!triggerEl) {
      return;
    }

    // If a menu is already open (e.g. user clicked another chart's
    // copy button before the previous menu dismissed), tear it down
    // first so we don't end up with stacked overlays and backdrops.
    // The previous owner's `onDismiss` fires so it can reset its
    // "open menu" sentinel; the caller's `onResult` is intentionally
    // NOT invoked because the previous menu was never "settled" on
    // an action — it was displaced.
    this.disposeActiveMenu(true);

    const overlayRef = this.overlay.create({
      positionStrategy: this.overlay
        .position()
        .flexibleConnectedTo(triggerEl)
        .withPositions([
          {
            originX: 'end',
            originY: 'bottom',
            overlayX: 'end',
            overlayY: 'top',
            offsetY: 4,
          },
          {
            // Fallback: open above the button if there is no room below.
            originX: 'end',
            originY: 'top',
            overlayX: 'end',
            overlayY: 'bottom',
            offsetY: -4,
          },
        ])
        .withPush(true)
        .withFlexibleDimensions(false),
      hasBackdrop: true,
      backdropClass: 'mermaid-menu-backdrop',
      scrollStrategy: this.overlay.scrollStrategies.reposition(),
      panelClass: 'mermaid-menu-panel-wrapper',
    });

    const portal = new ComponentPortal(MermaidActionsMenuComponent);
    const componentRef = overlayRef.attach(portal);

    let settled = false;
    const subs: Subscription[] = [];
    subs.push(componentRef.instance.action.subscribe((action: MermaidMenuAction) => settle(action)));
    subs.push(overlayRef.backdropClick().subscribe(() => settle(null)));
    subs.push(
      overlayRef.keydownEvents().subscribe((event) => {
        if (event.key === 'Escape') {
          settle(null);
        }
      }),
    );
    subs.push(
      overlayRef.detachments().subscribe(() => {
        this.openOverlays.delete(overlayRef);
        // Clear the active-menu pointer if this was the active one.
        // We don't fire `onDismiss` here because a normal settle() has
        // already done so (or will, on the next settle tick) — this
        // branch covers external detachments such as a parent overlay
        // being torn down.
        if (this.activeMenuOverlay === overlayRef) {
          this.activeMenuOverlay = null;
          this.activeMenuOnDismiss = null;
        }
      }),
    );

    const settle = (action: MermaidMenuAction | null) => {
      if (settled) {
        return;
      }
      settled = true;
      // Clear active-menu tracking BEFORE disposing so the detachment
      // observer above sees an already-null pointer and doesn't try to
      // double-clear.
      if (this.activeMenuOverlay === overlayRef) {
        this.activeMenuOverlay = null;
        this.activeMenuOnDismiss = null;
      }
      // Unsubscribe every overlay/dialog observable before tearing the
      // overlay down. Without this the subscriptions keep references to
      // the disposed `OverlayRef` until GC, leaking across many chart
      // opens during a long chat session.
      for (const sub of subs) {
        sub.unsubscribe();
      }
      overlayRef.dispose();
      this.openOverlays.delete(overlayRef);
      if (action === 'image') {
        void this.copySvgAsPng(context.svg, context.title).then(onResult);
      } else if (action === 'source') {
        void this.copyText(context.source, 'source').then(onResult);
      } else {
        onDismiss?.();
      }
    };

    this.openOverlays.add(overlayRef);
    // Promote this overlay to "active menu" — the single source of
    // truth for "which chart's menu is currently open". Subsequent
    // openCopyMenu() calls will dispose this one before creating
    // the next.
    this.activeMenuOverlay = overlayRef;
    this.activeMenuOnDismiss = onDismiss ?? null;
  }

  /**
   * Open the fullscreen Mermaid dialog for a chart.
   *
   * The dialog is sized 95vw × 95vh, gets both the project-wide
   * `dark-modal-panel` class (so existing dark-mode hooks in
   * `home.scss` / `jobs.scss` / `schedules.scss` keep working) AND a
   * dedicated `mermaid-fullscreen-panel` class that scopes the
   * fullscreen-only overrides (heavier backdrop blur, dialog shape).
   * Keeping the mermaid-only rules on the dedicated class prevents
   * them from leaking onto the 10+ other dialogs in the codebase
   * that also use `dark-modal-panel` (jobs, schedules, source-list,
   * mcp-server-list, agent-selector, queue-list).
   */
  openFullscreen(context: MermaidFullscreenDialogData): void {
    this.dialog.open(MermaidFullscreenDialogComponent, {
      panelClass: ['dark-modal-panel', 'mermaid-fullscreen-panel'],
      width: '95vw',
      height: '95vh',
      maxWidth: '95vw',
      maxHeight: '95vh',
      disableClose: false,
      autoFocus: 'first-tabbable',
      restoreFocus: true,
      data: context,
    });
  }

  /**
   * Copy the SVG markup of a chart to the system clipboard as a PNG.
   *
   * 1. Serialize the SVG with `XMLSerializer`, ensuring the `xmlns`
   *    namespace attribute is present so the resulting data URL is
   *    valid in isolation.
   * 2. Load the SVG into an `Image`, draw it onto a canvas sized to
   *    the SVG's intrinsic (or bounding) dimensions, then encode the
   *    canvas to a PNG blob.
   * 3. Wrap the blob in a `ClipboardItem` and call
   *    `navigator.clipboard.write` so the user can paste the chart
   *    into any image-aware application.
   *
   * Falls back to logging + status text on browsers where the async
   * clipboard API refuses image writes (Firefox in particular).
   */
  async copySvgAsPng(svg: string, title?: string): Promise<MermaidCopyResult> {
    if (!svg) {
      return { success: false, action: 'image', message: 'No chart to copy' };
    }

    // Some browsers require a definite width/height on the root <svg>
    // before they will draw it onto a canvas. Read the chart's on-page
    // size first and fall back to the inline viewBox.
    const parser = new DOMParser();
    const parsed = parser.parseFromString(svg, 'image/svg+xml');
    const svgEl = parsed.documentElement;
    if (!svgEl || svgEl.nodeName !== 'svg') {
      return { success: false, action: 'image', message: 'Invalid SVG' };
    }

    const viewBox = svgEl.getAttribute('viewBox');
    const resolvedWidth = this.resolveDimension(svgEl, viewBox, 'width');
    const resolvedHeight = this.resolveDimension(svgEl, viewBox, 'height');
    if (resolvedWidth === null || resolvedHeight === null) {
      // All three resolution paths failed for at least one axis. Fall
      // back to a sensible default so the copy still produces a usable
      // image rather than rendering a 0x0 canvas (which would throw
      // `toBlob` errors on some browsers).
      console.warn(
        'Mermaid copySvgAsPng: could not determine SVG dimensions from attrs, bounding rect, or viewBox; using 800x600',
      );
    }
    const width = resolvedWidth ?? 800;
    const height = resolvedHeight ?? 600;

    if (!svgEl.getAttribute('xmlns')) {
      svgEl.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    }
    svgEl.setAttribute('width', String(width));
    svgEl.setAttribute('height', String(height));

    const serialized = new XMLSerializer().serializeToString(svgEl);
    // Use encodeURIComponent rather than btoa() so the data URL survives
    // multibyte characters (mermaid emits some UTF-8 text inside labels).
    const encoded = encodeURIComponent(serialized)
      .replace(/'/g, '%27')
      .replace(/"/g, '%22');
    const dataUrl = `data:image/svg+xml;charset=utf-8,${encoded}`;

    try {
      const image = await this.loadImage(dataUrl);
      // Cap the output at ~2x the intrinsic size — large diagrams
      // would otherwise balloon the clipboard blob to many MB.
      const scale = Math.min(2, Math.max(1, 1200 / Math.max(width, height)));
      const canvas = this.document.createElement('canvas');
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        return { success: false, action: 'image', message: 'Canvas unsupported' };
      }
      // Painted onto a light background so the (dark-themed) chart is
      // readable when pasted into apps that don't respect transparency.
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

      const blob = await new Promise<Blob | null>((resolve) => {
        canvas.toBlob((b) => resolve(b), 'image/png');
      });
      if (!blob) {
        return { success: false, action: 'image', message: 'PNG encoding failed' };
      }

      if (!navigator?.clipboard?.write || typeof ClipboardItem === 'undefined') {
        return {
          success: false,
          action: 'image',
          message: 'Image clipboard not supported in this browser',
        };
      }
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      return {
        success: true,
        action: 'image',
        message: title ? `Copied "${title}" as image` : 'Copied as image',
      };
    } catch (err) {
      console.error('Failed to copy mermaid chart as image', err);
      return {
        success: false,
        action: 'image',
        message: err instanceof Error ? err.message : 'Copy failed',
      };
    }
  }

  /**
   * Copy arbitrary text to the system clipboard. Used for the
   * "Copy Mermaid Source" action; the fullscreen dialog also calls
   * this for its inline copy button.
   */
  async copyText(text: string, kind: 'source' = 'source'): Promise<MermaidCopyResult> {
    if (!text) {
      return { success: false, action: kind, message: 'Nothing to copy' };
    }
    try {
      if (!navigator?.clipboard?.writeText) {
        throw new Error('Clipboard API unavailable');
      }
      await navigator.clipboard.writeText(text);
      return { success: true, action: kind, message: 'Source copied to clipboard' };
    } catch (err) {
      console.error('Failed to copy text', err);
      return {
        success: false,
        action: kind,
        message: err instanceof Error ? err.message : 'Copy failed',
      };
    }
  }

  /**
   * Decode an image (or fail) into a Promise<HTMLImageElement>.
   * Extracted so the PNG-copy flow stays readable.
   */
  private loadImage(src: string): Promise<HTMLImageElement> {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => resolve(img);
      img.onerror = (err) => reject(err instanceof Event ? new Error('image load failed') : err);
      img.src = src;
    });
  }

  /**
   * Resolve a usable numeric dimension for a chart's `width` or
   * `height` axis. The resolution chain is:
   *
   *   1. The SVG attribute (`width`/`height`) parsed strictly: we
   *      accept only an unsigned integer/decimal, optionally followed
   *      by a `px` suffix. Values like `"100%"`, `"10em"`, `"5vw"`,
   *      or `"auto"` are REJECTED — `parseInt` would silently coerce
   *      them to `NaN` or to a meaningless number, which is exactly
   *      the bug W1 is fixing.
   *   2. The live `getBoundingClientRect()` of the SVG element.
   *      This works for SVGs already mounted in the DOM. The SVG
   *      we receive here is parsed from a serialized string and is
   *      detached, so the rect will almost always be `{0, 0, 0, 0}` —
   *      but we still try, because in some flows (e.g. when callers
   *      pass an already-mounted element via a different code path
   *      in the future) the rect is the most accurate measurement.
   *      We guard the call with `typeof === 'function'` because
   *      `SVGSVGElement.prototype.getBoundingClientRect` is not
   *      guaranteed on every test/SSR environment.
   *   3. The `viewBox` attribute, parsed as `x y w h`. Mermaid always
   *      emits a viewBox so this is the most reliable fallback for
   *      detached SVGs.
   *
   * Returns `null` if every step fails; the caller is responsible
   * for applying the final 800x600 default and warning.
   */
  private resolveDimension(
    svgEl: Element,
    viewBox: string | null,
    axis: 'width' | 'height',
  ): number | null {
    // 1. Strict attribute parse: numeric, optional decimals, optional px.
    const attr = svgEl.getAttribute(axis);
    if (attr) {
      const match = /^\d+(\.\d+)?(px)?$/i.exec(attr.trim());
      if (match) {
        const num = Number(match[0].replace(/px$/i, ''));
        if (Number.isFinite(num) && num > 0) {
          return num;
        }
      }
    }

    // 2. Live bounding rect (best-effort; detached SVGs usually report 0).
    if (typeof svgEl.getBoundingClientRect === 'function') {
      try {
        const rect = svgEl.getBoundingClientRect();
        const live = axis === 'width' ? rect.width : rect.height;
        if (Number.isFinite(live) && live > 0) {
          return live;
        }
      } catch {
        // Some environments throw when measuring detached nodes; ignore.
      }
    }

    // 3. viewBox: `<min-x> <min-y> <width> <height>`.
    if (viewBox) {
      const parts = viewBox.trim().split(/[\s,]+/).map(Number);
      if (parts.length === 4 && parts.every((n) => Number.isFinite(n))) {
        const vbValue = axis === 'width' ? parts[2] : parts[3];
        if (vbValue > 0) {
          return vbValue;
        }
      }
    }

    return null;
  }
}
