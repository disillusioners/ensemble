import { Injectable, inject } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import {
  LiveViewDialogComponent,
  LiveViewDialogData,
} from '../components/live-view-dialog/live-view-dialog.component';
import { parseLiveViewUrl, deriveLiveViewTitle } from '../constants/live-view-url';

/**
 * Parallel service to ``ImageViewerActionsService`` for the
 * live-view WebView dialog. Kept separate so the chat-interface
 * (which owns the chip click bindings) does not directly depend
 * on ``MatDialog`` — the service brokers the call and centralizes
 * the dialog open-config so a future change to the dialog shape
 * does not bleed into the chip renderer.
 *
 * Two roles:
 *
 *  1. ``openViewer(href)`` — the chat-interface's chip click
 *     entry point. Re-runs ``parseLiveViewUrl`` as a defense
 *     in depth (the chip is only built from a URL that
 *     already passed the matcher, but the re-parse guards
 *     against a future change that widens the chip path
 *     without re-tightening the dialog gate). A URL that
 *     does NOT re-parse is silently dropped (returns
 *     ``null``) — the dialog is never opened on a URL the
 *     matcher would reject.
 *
 *  2. ``tryOpenViewer(href)`` — the explicit try / no-op
 *     variant. Callers that want to silently ignore a
 *     non-live-view URL (e.g. an `<a href>` that
 *     happens to also be a chip candidate but the
 *     upstream chain has already validated) use this.
 *     ``openViewer`` is the more opinionated default; both
 *     share the same shape.
 *
 * The open-config mirrors ``ImageViewerActionsService.openViewer``
 * at lines 44-60 EXACTLY, swapping:
 *   - the component (ImageViewerDialogComponent →
 *     LiveViewDialogComponent)
 *   - the second ``panelClass`` entry
 *     (``image-viewer-panel`` → ``live-view-panel``) — the
 *     two dedicated classes keep the fullscreen-only rules
 *     from leaking onto each other.
 *   - the dimensions (95vw / 95vh → 95vw / 90vh — the live
 *     view is slightly shorter so the page mockup leaves
 *     room for browser chrome / taskbar at the bottom of
 *     the viewport).
 */
@Injectable({ providedIn: 'root' })
export class LiveViewActionsService {
  private readonly dialog = inject(MatDialog);

  /**
   * Open the WebView dialog for a click on a live-view chip.
   *
   * Returns the opened ``MatDialogRef`` (or ``null`` if the
   * href is not a valid live-view URL — the caller can
   * inspect the return value if it wants to react to the
   * success / fail split).
   */
  openViewer(href: string) {
    return this.tryOpenViewer(href);
  }

  /**
   * The internal re-parse + open flow. ``openViewer`` is the
   * public alias; both exist so a future call site that
   * wants the explicit try / no-op shape can spell it
   * directly.
   */
  tryOpenViewer(href: string) {
    const parsed = parseLiveViewUrl(href);
    if (parsed === null) {
      // The chip is only built from a URL that already passed
      // the matcher; if the re-parse fails, the upstream chain
      // has been corrupted. Silently drop — never open a
      // dialog on an invalid href.
      return null;
    }
    const payload: LiveViewDialogData = {
      src: parsed.href,
      title: deriveLiveViewTitle(parsed),
      root: parsed.root,
      relPath: parsed.relPath,
    };
    return this.dialog.open(LiveViewDialogComponent, {
      panelClass: ['dark-modal-panel', 'live-view-panel'],
      width: '95vw',
      height: '90vh',
      maxWidth: '95vw',
      maxHeight: '90vh',
      disableClose: false,
      autoFocus: 'first-tabbable',
      restoreFocus: true,
      data: payload,
    });
  }
}
