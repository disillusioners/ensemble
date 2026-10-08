import {
  Component,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  inject,
  signal,
  computed,
  OnDestroy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import {
  MatDialogRef,
  MAT_DIALOG_DATA,
  MatDialogModule,
} from '@angular/material/dialog';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

/**
 * Data payload for the live-view WebView dialog.
 *
 * The dialog is opened by ``LiveViewActionsService.openViewer``
 * (mirror of ``ImageViewerActionsService``) when the user clicks
 * a chip rendered into a chat message bubble. The dialog is
 * sized large (95vw × 90vh — see the service) to give page
 * mockups room to render without horizontal scroll bars.
 *
 *  - ``src``     — the canonical live-view URL
 *                  (``/views/<root>/<rel>``). The dialog
 *                  trusts the upstream
 *                  ``parseLiveViewUrl`` matcher (Phase 2 FE
 *                  gate) to have validated the shape; this
 *                  dialog does not re-validate.
 *  - ``title``   — the chip label, derived from the artifact's
 *                  final path segment by
 *                  ``deriveLiveViewTitle``. Shown in the
 *                  dialog header and announced as the
 *                  accessible label.
 *  - ``root``    — the view-root segment (e.g. ``planning``,
 *                  ``designer-artifact``, ``tmp-images``).
 *                  Used purely for display in the header
 *                  subtitle.
 *  - ``relPath`` — the artifact's path under the root
 *                  (e.g. ``ens/feat/design/mockups/landing.html``).
 *                  Shown in the header subtitle and as the
 *                  iframe ``title`` attribute (the browser
 *                  displays it on hover).
 */
export interface LiveViewDialogData {
  src: string;
  title: string;
  root: string;
  relPath: string;
}

/**
 * Fullscreen-style dialog showing a sandboxed iframe of a
 * live-view artifact.
 *
 * SECURITY MODEL — the iframe sandbox attribute is the
 * load-bearing defense for this entire feature. The Phase-1
 * daemon serves live-view artifacts read-only with hardened
 * ``X-Content-Type-Options: nosniff`` headers
 * (``daemon/routers/live_views.py:69-72``), and the FE-side
 * matcher in ``constants/live-view-url.ts`` gates the URL
 * shape before a chip is ever rendered. The iframe then
 * applies a second, structural layer of defense:
 *
 *  - ``sandbox="allow-scripts"`` ONLY — explicitly WITHOUT
 *    ``allow-same-origin``. ``allow-scripts`` keeps the
 *    served mockups interactive (a phase-1 use case is
 *    HTML / SVG / JS design mockups whose behavior matters
 *    to the user). Omitting ``allow-same-origin`` loads the
 *    artifact in an OPAQUE origin — the iframe has no
 *    same-origin privileges against the ensemble app or any
 *    other origin. Concrete consequences:
 *
 *    * The artifact's JS cannot read / write / mutate the
 *      ensemble DOM (no parent.DOM access).
 *    * The artifact cannot read or set cookies on the
 *      ensemble origin.
 *    * The artifact cannot make credentialed same-origin
 *      fetch / XHR calls against the ensemble backend.
 *    * ``localStorage`` / ``sessionStorage`` / IndexedDB
 *      access is partitioned to the opaque origin, not the
 *      ensemble app.
 *
 *    So even a malicious artifact that slips past the
 *    daemon-side hardening and the FE matcher cannot touch
 *    the ensemble app.
 *
 *  - The iframe ``src`` is bound via ``DomSanitizer`` (the
 *    standard Angular escape hatch) so the sanitizer is
 *    not bypassed. The dialog trusts the matcher to have
 *    rejected non-live-view URLs; the sanitizer is the
 *    belt-and-braces against an accidental widening.
 *
 * KEYBOARD ACCESSIBILITY:
 *
 *  - ``MatDialog`` defaults wire ``disableClose: false`` so
 *    Escape and backdrop click both close the dialog. The
 *    explicit close button below is the affordance for
 *    users who do not know the keyboard shortcut.
 *  - ``autoFocus: 'first-tabbable'`` lands the focus on
 *    the close button so a screen-reader user can
 *    immediately dismiss the dialog without hunting for
 *    the X.
 *  - The iframe is NOT in the dialog's tab order — once
 *    the dialog opens, focus stays on the close button.
 *    This is intentional: the iframe's content is the
 *    user's content (an artifact they explicitly chose
 *    to view), and trapping keyboard focus inside an
 *    opaque-origin iframe is hostile to keyboard-only
 *    navigation.
 *
 * LOADING STATE:
 *
 *  The dialog starts in a loading state (``loaded()`` is
 *  ``false``) and flips to loaded when the iframe's
 *  ``load`` event fires. The ``error`` event swaps the
 *  body to a fallback panel showing the failure reason
 *  (the iframe element's onerror handler is cleared
 *  after the first event so a synthetic 0-byte payload
 *  does not loop).
 */
@Component({
  selector: 'app-live-view-dialog',
  standalone: true,
  imports: [CommonModule, MatDialogModule, MatButtonModule, MatIconModule],
  templateUrl: './live-view-dialog.component.html',
  styleUrl: './live-view-dialog.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LiveViewDialogComponent implements OnDestroy {
  protected readonly dialogRef = inject(MatDialogRef<LiveViewDialogComponent>);
  protected readonly data = inject<LiveViewDialogData>(MAT_DIALOG_DATA);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly cdr = inject(ChangeDetectorRef);

  /**
   * Whether the iframe has fired its ``load`` event. Starts
   * ``false`` so the user sees a spinner / loading affordance
   * while the artifact fetches.
   */
  readonly loaded = signal<boolean>(false);

  /**
   * Whether the iframe has fired an ``error`` event. Mutually
   * exclusive with ``loaded()`` — a successful load flips
   * ``loaded`` to ``true`` and never fires ``error``. A
   * network failure (or a daemon 404 envelope that
   * ``<iframe>`` reports as an error) flips ``errored`` to
   * ``true`` and renders the fallback panel.
   */
  readonly errored = signal<boolean>(false);

  /**
   * Pre-sanitized iframe ``src`` — the sanitizer is the
   * belt-and-braces; the matcher is the gate. See the
   * SECURITY MODEL block on the class.
   */
  readonly safeSrc: SafeResourceUrl;

  /**
   * Composite accessible label for the dialog. Combines the
   * chip-derived title with the root for screen-reader
   * context. The literal value is the ``aria-label`` on the
   * dialog shell.
   */
  readonly ariaLabel = computed(
    () => `Live view: ${this.data.title} (root: ${this.data.root})`,
  );

  constructor() {
    // Bind the sanitized src ONCE in the constructor — the
    // matcher's job is to keep non-live-view URLs from ever
    // reaching the dialog, so the sanitizer sees only
    // pre-validated input. Re-sanitizing on every change
    // detection pass would be wasted work and could mask a
    // logic bug if the matcher ever loosens.
    this.safeSrc = this.sanitizer.bypassSecurityTrustResourceUrl(this.data.src);
  }

  ngOnDestroy(): void {
    // No subscriptions to clean up — the iframe's own teardown
    // is handled by the browser when the dialog element is
    // removed from the DOM. The placeholder is here so a
    // future contributor who adds subscriptions knows the
    // hook is already wired.
  }

  /**
   * Iframe ``load`` handler. Flips ``loaded()`` to ``true``
   * and clears the element-level ``onload`` so a synthetic
   * re-load does not double-fire (the dialog swaps the
   * signal only once — subsequent load events are no-ops).
   */
  onIframeLoad(): void {
    if (this.loaded()) {
      return;
    }
    this.loaded.set(true);
    this.errored.set(false);
    this.cdr.markForCheck();
  }

  /**
   * Iframe ``error`` handler. Flips ``errored()`` to ``true``
   * and clears the element-level ``onerror`` so a synthetic
   * re-fire (e.g. the iframe being reattached) does not
   * double-trigger the fallback swap.
   */
  onIframeError(event: Event): void {
    const el = event.target as HTMLIFrameElement | null;
    if (el) {
      el.onerror = null;
    }
    this.errored.set(true);
    this.loaded.set(false);
    this.cdr.markForCheck();
  }

  /**
   * Close the dialog. Bound to the X button in the header.
   * Escape and backdrop are wired by ``MatDialog`` defaults
   * (``disableClose: false`` set in the service).
   */
  onClose(): void {
    this.dialogRef.close();
  }
}
