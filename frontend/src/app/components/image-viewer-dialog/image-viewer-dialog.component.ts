import {
  Component,
  ChangeDetectionStrategy,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';

/**
 * Data payload for the image-viewer dialog.
 *
 *  - `src`        — the image URL (a legacy ``data:image/...`` data URI
 *                   OR a canonical server-ref matching
 *                   ``TMP_IMAGE_REF_PREFIX`` from
 *                   ``constants/image-ref``). The dialog treats both
 *                   forms identically — it just binds ``<img [src]>``
 *                   and lets the same onerror handler fire if the
 *                   image fails to load.
 *  - `alt`        — accessible label for the image (defaults to
 *                   "Attached image" if missing).
 *  - `message_id` — the message whose thumbnail was clicked. Kept for
 *                   telemetry / future per-message dialog state; today
 *                   it is a no-op in the dialog body itself.
 */
export interface ImageViewerDialogData {
  src: string;
  alt?: string;
  message_id: string;
}

/**
 * Pinned fallback image rendered when the dialog's own ``<img>`` fails
 * to load (e.g. the user opened the dialog on a 410'd image-ref after
 * the 30-day tmp-image cleanup).
 *
 * The literal SVG markup is duplicated here so it can be exposed for
 * identity-grep mirror-parity in ``image-viewer-dialog.component.spec.ts``.
 * The encoded data-URI form is what the dialog actually binds to the
 * ``<img [src]>``. Both MUST stay byte-identical to the pins in the
 * spec — a future contributor who changes the colors must update both
 * sites.
 */
const FALLBACK_IMAGE_SVG_MARKUP =
  "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='200' height='200'>" +
  "<rect width='24' height='24' fill='#374151'/>" +
  "<path d='M3 5h18v14H3z' fill='none' stroke='#9ca3af' stroke-width='1.5'/>" +
  "<circle cx='8' cy='9' r='1.5' fill='#9ca3af'/>" +
  "<path d='M3 17l5-5 4 4 3-3 6 6v2H3z' fill='#6b7280'/>" +
  "<line x1='4' y1='4' x2='20' y2='20' stroke='#ef4444' stroke-width='1.5'/>" +
  "</svg>";
const FALLBACK_IMAGE_SRC =
  "data:image/svg+xml;utf8," +
  // encodeURIComponent encodes the structural chars (< > # space) but
  // leaves apostrophes untouched — that's fine inside an attribute
  // value delimited by single quotes. We deliberately DO NOT use
  // base64 here: keeping the markup in the URI makes it human-readable
  // when the fallback renders during devtools inspection.
  encodeURIComponent(FALLBACK_IMAGE_SVG_MARKUP);

/**
 * Fullscreen-style dialog shown when the user clicks an attached
 * image thumbnail in a user-message bubble.
 *
 * Modeled on ``MermaidFullscreenDialogComponent`` (same shell +
 * dialog shape, same panel class) but DOES NOT use
 * ``bypassSecurityTrustHtml`` — the image is bound via plain
 * ``<img [src]>`` which goes through Angular's URL sanitizer
 * automatically. The dialog body has its OWN ``(error)`` handler that
 * swaps the broken-image element to the same pinned fallback SVG that
 * the bubble thumbnail uses (plan risk #4: the dialog body must not
 * show a broken-image icon if the user opens a 410'd ref).
 *
 * Keyboard accessibility: close button + Escape + backdrop are wired
 * by ``MatDialog`` defaults; the underlying thumbnail is NOT in the
 * keyboard tab order today — that's an outstanding accessibility
 * follow-up (plan Out-of-Scope §"Accessibility for keyboard navigation
 * onto thumbnails").
 */
@Component({
  selector: 'app-image-viewer-dialog',
  standalone: true,
  imports: [CommonModule, MatDialogModule, MatButtonModule, MatIconModule],
  templateUrl: './image-viewer-dialog.html',
  styleUrl: './image-viewer-dialog.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ImageViewerDialogComponent {
  protected readonly dialogRef = inject(MatDialogRef<ImageViewerDialogComponent>);
  protected readonly data = inject<ImageViewerDialogData>(MAT_DIALOG_DATA);

  /**
   * The src the dialog body currently binds to its ``<img>``. Starts
   * as ``data.src``; swapped to the fallback SVG on the first
   * ``(error)`` event so a 410'd image does not show a broken-image
   * icon inside the dialog. Mutable via the (error) handler below.
   *
   * Wrapped in a signal so OnPush change detection sees the swap —
   * assigning ``<img>.src`` directly would mutate the DOM without
   * triggering a CD pass, which is fine for the visible attribute but
   * breaks any downstream template binding that might want to react
   * (e.g. a future "retry" button gated on ``imgFailed()``).
   *
   * Marked `public` (not `protected`) so the spec file can read the
   * post-error value without a Testable subclass — the signals are
   * already exposed via the template binding so callers within the
   * component subtree see them via the same surface.
   */
  readonly currentSrc = signal<string>('');
  readonly imgFailed = signal<boolean>(false);

  /**
   * Accessible label — defaults to ``"Attached image"`` when the
   * bubble did not provide one (matches the bubble template's
   * ``alt="Attached image"``).
   */
  readonly altText = signal<string>('Attached image');

  constructor() {
    const payload = this.data ?? { src: '', alt: '', message_id: '' };
    this.currentSrc.set(payload.src || '');
    const alt = (payload.alt ?? '').trim();
    if (alt) {
      this.altText.set(alt);
    }
  }

  onClose(): void {
    this.dialogRef.close();
  }

  /**
   * Dialog-body ``<img (error)>`` handler. Identical semantics to the
   * bubble-level ``onImageError`` on ``ChatInterfaceComponent`` (the
   * latter also records the failure in ``failedImages`` so a re-render
   * does NOT re-fetch — the dialog body does NOT need that because
   * the dialog is a fresh component instance per open).
   *
   * The swap to the fallback src is idempotent: a second ``error``
   * event for the same element is a no-op (``imgFailed()`` is already
   * ``true`` and ``currentSrc()`` is already the fallback). The dialog
   * has no model-side state to protect, so we don't need the signal
   * suppression dance — but we still short-circuit to keep the
   * handler cheap.
   *
   * Public (not protected) so the spec file can drive the error path
   * with a stub event.
   */
  onImgError(event: Event): void {
    if (this.imgFailed()) {
      return;
    }
    const imgEl = event.target as HTMLImageElement | null;
    if (imgEl) {
      // Stop the native browser from firing further `error` events on
      // this element while it tries to load the same broken src.
      imgEl.onerror = null;
      imgEl.src = FALLBACK_IMAGE_SRC;
    }
    this.currentSrc.set(FALLBACK_IMAGE_SRC);
    this.imgFailed.set(true);
  }
}

/**
 * Re-exports of the pinned fallback values for identity-grep
 * mirror-parity in the spec. Consumers MUST NOT mutate these.
 */
export const IMAGE_VIEWER_FALLBACK_SVG_MARKUP = FALLBACK_IMAGE_SVG_MARKUP;
export const IMAGE_VIEWER_FALLBACK_SRC = FALLBACK_IMAGE_SRC;
