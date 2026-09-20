import { Injectable, inject } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import {
  ImageViewerDialogComponent,
  ImageViewerDialogData,
} from '../components/image-viewer-dialog/image-viewer-dialog.component';

/**
 * Parallel service to ``MermaidActionsService`` for the image-viewer
 * dialog. Kept separate so the two surfaces do not bleed open-config
 * (the Mermaid service also owns CDK overlay menu state — coupling
 * the image viewer to it would be a needless expansion of that
 * service's responsibilities).
 *
 * The open-config below mirrors
 * ``MermaidActionsService.openFullscreen`` at lines 256-267 EXACTLY,
 * swapping:
 *   - the component (Mermaid → Image)
 *   - the second ``panelClass`` entry (mermaid-fullscreen-panel →
 *     image-viewer-panel) — the two dedicated classes keep the
 *     fullscreen-only rules from leaking onto each other.
 *
 * ``providedIn: 'root'`` — owns no per-component state, just brokers
 * calls between the chat-interface (which owns the click bindings on
 * the rendered thumbnails) and the Angular Material dialog.
 */
@Injectable({ providedIn: 'root' })
export class ImageViewerActionsService {
  private readonly dialog = inject(MatDialog);

  /**
   * Open the fullscreen-style image-viewer dialog for a thumbnail
   * click. The dialog is sized 95vw × 95vh and carries the
   * project-wide ``dark-modal-panel`` class (so existing dark-mode
   * hooks in ``home.scss`` keep working) PLUS a dedicated
   * ``image-viewer-panel`` class that scopes the fullscreen-only
   * overrides (dialog shape + blur).
   *
   * ``src`` may be a legacy ``data:image/...`` data URI OR a canonical
   * server-ref matching ``TMP_IMAGE_REF_PREFIX`` — the dialog renders
   * both forms identically via plain ``<img [src]>`` (no
   * ``bypassSecurityTrustHtml``).
   */
  openViewer(src: string, messageId: string): void {
    const payload: ImageViewerDialogData = {
      src,
      message_id: messageId,
    };
    this.dialog.open(ImageViewerDialogComponent, {
      panelClass: ['dark-modal-panel', 'image-viewer-panel'],
      width: '95vw',
      height: '95vh',
      maxWidth: '95vw',
      maxHeight: '95vh',
      disableClose: false,
      autoFocus: 'first-tabbable',
      restoreFocus: true,
      data: payload,
    });
  }
}
