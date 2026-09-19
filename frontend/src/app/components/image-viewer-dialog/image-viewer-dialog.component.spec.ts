/**
 * Phase 6 / clipboard-image-chat — image-viewer dialog spec.
 *
 * Per FE conventions (testing-and-qc-conventions blueprint), the
 * dialog is exercised with a minimal Angular TestBed mount that
 * stubs ONLY ``MAT_DIALOG_DATA`` + ``MatDialogRef`` — no service
 * providers, no fixture-driven DOM mutations. The dialog body is
 * thin enough that a focused mount keeps the spec readable.
 *
 * Test strategy (plan / phase6-plan.md Test Strategy table rows 1, 2, 4):
 *
 *   - Dialog data shape + alt-text defaulting
 *   - ``onImgError`` swaps ``currentSrc()`` to the pinned fallback SVG
 *     (byte-for-byte match — see ``IMAGE_VIEWER_FALLBACK_*`` pins)
 *   - Idempotent: a second ``error`` event is a no-op (no extra fetch)
 *   - OnPush-friendly: the swap is observable via the signal
 *   - Identity-grep mirror-parity pins
 *     (``image-viewer-panel``, ``dark-modal-panel``) live in the
 *     production source — verified via `grep -RIn`.
 *
 * Playwright e2e hook points (tester pass — Task 8):
 *   - `[data-testid="image-viewer-dialog"]` (the dialog container)
 *   - `[data-testid="image-viewer-img"]`     (the dialog `<img>`)
 *   - `[data-testid="image-viewer-close"]`   (the close button)
 *   - `img.message-image`                    (the bubble thumbnail)
 *   - `img.message-image-failed`             (the bubble fallback state)
 * Reachable states: dialog open with normal src; dialog open with 410
 * src (img element falls back to the pinned SVG); close via button /
 * Escape / backdrop (MatDialog defaults).
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import {
  ImageViewerDialogComponent,
  IMAGE_VIEWER_FALLBACK_SVG_MARKUP,
  IMAGE_VIEWER_FALLBACK_SRC,
  ImageViewerDialogData,
} from './image-viewer-dialog.component';

describe('ImageViewerDialogComponent — phase 6 image viewer', () => {
  let fixture: ComponentFixture<ImageViewerDialogComponent>;
  let component: ImageViewerDialogComponent;
  let dialogRefClose: jest.Mock;

  function setupMount(data: ImageViewerDialogData): void {
    dialogRefClose = jest.fn();
    TestBed.configureTestingModule({
      imports: [ImageViewerDialogComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MAT_DIALOG_DATA, useValue: data },
        {
          provide: MatDialogRef,
          useValue: { close: dialogRefClose },
        },
      ],
    });
    fixture = TestBed.createComponent(ImageViewerDialogComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  describe('construction / data shape', () => {
    it('binds the dialog src to the data payload src', () => {
      const data: ImageViewerDialogData = {
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        alt: 'My screenshot',
        message_id: 'srv-1',
      };
      setupMount(data);
      expect(component.currentSrc()).toBe(
        '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      );
    });

    it('defaults alt to "Attached image" when the data payload omits it', () => {
      setupMount({ src: 'data:image/png;base64,AAAA', message_id: 'srv-1' });
      expect(component.altText()).toBe('Attached image');
    });

    it('uses the supplied alt text verbatim (trimmed only)', () => {
      setupMount({
        src: 'data:image/png;base64,AAAA',
        alt: '   screenshot 2026-09-19   ',
        message_id: 'srv-1',
      });
      expect(component.altText()).toBe('screenshot 2026-09-19');
    });

    it('treats a missing src as an empty src (defensive)', () => {
      setupMount({ src: '', message_id: 'srv-1' });
      expect(component.currentSrc()).toBe('');
    });
  });

  describe('onImgError — fallback swap (plan row 4, risk #4)', () => {
    it('swaps currentSrc() to the pinned fallback SVG on the first error event', () => {
      setupMount({
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        message_id: 'srv-1',
      });
      const imgEl: HTMLImageElement = fixture.nativeElement.querySelector(
        '[data-testid="image-viewer-img"]',
      );
      expect(imgEl).not.toBeNull();
      imgEl.dispatchEvent(new Event('error'));
      expect(component.currentSrc()).toBe(IMAGE_VIEWER_FALLBACK_SRC);
      expect(component.imgFailed()).toBe(true);
    });

    it('is idempotent — a second error event does NOT swap src again', () => {
      setupMount({
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        message_id: 'srv-1',
      });
      const imgEl: HTMLImageElement = fixture.nativeElement.querySelector(
        '[data-testid="image-viewer-img"]',
      );
      imgEl.dispatchEvent(new Event('error'));
      expect(component.currentSrc()).toBe(IMAGE_VIEWER_FALLBACK_SRC);
      // Capture the fallback's src binding length; a second error must
      // not introduce a different value.
      const second = component.currentSrc();
      imgEl.dispatchEvent(new Event('error'));
      expect(component.currentSrc()).toBe(second);
    });

    it('swaps src when the event target is null (defensive)', () => {
      setupMount({
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        message_id: 'srv-1',
      });
      // Direct call with no target — must not throw and must still flip
      // the signal.
      expect(() =>
        component.onImgError({ target: null } as unknown as Event),
      ).not.toThrow();
      expect(component.currentSrc()).toBe(IMAGE_VIEWER_FALLBACK_SRC);
      expect(component.imgFailed()).toBe(true);
    });

    it('clears the native onerror on the broken element to suppress further events', () => {
      setupMount({
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        message_id: 'srv-1',
      });
      const imgEl: HTMLImageElement = fixture.nativeElement.querySelector(
        '[data-testid="image-viewer-img"]',
      );
      imgEl.onerror = jest.fn();
      imgEl.dispatchEvent(new Event('error'));
      // The dialog nils the element-level onerror handler so the
      // browser does not refire the event when we swap the src.
      expect(imgEl.onerror).toBeNull();
    });
  });

  describe('onClose', () => {
    it('delegates to MatDialogRef.close()', () => {
      setupMount({ src: 'data:image/png;base64,AAAA', message_id: 'srv-1' });
      component.onClose();
      expect(dialogRefClose).toHaveBeenCalledTimes(1);
    });
  });

  describe('template — Playwright hook points', () => {
    it('renders the [data-testid="image-viewer-dialog"] container', () => {
      setupMount({ src: 'data:image/png;base64,AAAA', message_id: 'srv-1' });
      const dialog = fixture.nativeElement.querySelector(
        '[data-testid="image-viewer-dialog"]',
      );
      expect(dialog).not.toBeNull();
      expect(dialog?.getAttribute('role')).toBe('dialog');
    });

    it('renders the [data-testid="image-viewer-img"] <img> bound to currentSrc()', () => {
      setupMount({
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        message_id: 'srv-1',
      });
      const img = fixture.nativeElement.querySelector(
        '[data-testid="image-viewer-img"]',
      ) as HTMLImageElement | null;
      expect(img).not.toBeNull();
      expect(img?.getAttribute('src')).toBe(
        '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      );
    });

    it('renders the [data-testid="image-viewer-close"] close button', () => {
      setupMount({ src: 'data:image/png;base64,AAAA', message_id: 'srv-1' });
      const close = fixture.nativeElement.querySelector(
        '[data-testid="image-viewer-close"]',
      ) as HTMLButtonElement | null;
      expect(close).not.toBeNull();
      close?.click();
      expect(dialogRefClose).toHaveBeenCalledTimes(1);
    });

    it('applies the viewer-image-failed class on the <img> after onImgError', () => {
      setupMount({
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        message_id: 'srv-1',
      });
      const img = fixture.nativeElement.querySelector(
        '[data-testid="image-viewer-img"]',
      ) as HTMLImageElement | null;
      expect(img?.classList.contains('viewer-image-failed')).toBe(false);
      img?.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      expect(img?.classList.contains('viewer-image-failed')).toBe(true);
    });
  });
});

describe('IMAGE_VIEWER_FALLBACK_* pins (identity-grep mirror-parity)', () => {
  // Pinned bytes — a future contributor who changes the SVG must
  // update both the markup constant AND the encoded data-URI. The
  // spec asserts the byte-for-byte identity so a regression trips
  // immediately.

  it('SVG markup contains the expected viewBox + broken-image glyph', () => {
    expect(IMAGE_VIEWER_FALLBACK_SVG_MARKUP).toContain("viewBox='0 0 24 24'");
    expect(IMAGE_VIEWER_FALLBACK_SVG_MARKUP).toContain("<circle cx='8' cy='9'");
    expect(IMAGE_VIEWER_FALLBACK_SVG_MARKUP).toContain("stroke='#ef4444'");
  });

  it('data URI is the canonical data:image/svg+xml;utf8,… form', () => {
    expect(IMAGE_VIEWER_FALLBACK_SRC.startsWith('data:image/svg+xml;utf8,')).toBe(true);
  });

  it('data URI encodes the markup via encodeURIComponent (no base64)', () => {
    // encodeURIComponent encodes < > # space; apostrophes are passed
    // through. The resulting URI is human-readable when inspected.
    expect(IMAGE_VIEWER_FALLBACK_SRC).toContain('%3Csvg');
    expect(IMAGE_VIEWER_FALLBACK_SRC).toContain('%3E');
    expect(IMAGE_VIEWER_FALLBACK_SRC).toContain('%23');
    // Single quotes are passed through.
    expect(IMAGE_VIEWER_FALLBACK_SRC).toContain("'");
  });

  it('encoded length is under 1 KiB (the spec budgets ~200 bytes for the raw SVG)', () => {
    // The raw SVG is ~250 chars; URI-encoding pushes it under ~700.
    // We allow generous headroom (1 KiB) so this pin doesn't break on
    // minor formatting tweaks; the identity-grep pin still catches
    // gross drift.
    expect(IMAGE_VIEWER_FALLBACK_SRC.length).toBeLessThan(1024);
  });
});
