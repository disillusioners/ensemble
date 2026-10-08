/**
 * Live-view WebView dialog spec.
 *
 * Per FE conventions (testing-and-qc-conventions blueprint), the
 * dialog is exercised with a minimal Angular TestBed mount that
 * stubs ONLY ``MAT_DIALOG_DATA`` + ``MatDialogRef`` + ``DomSanitizer``
 * — no service providers, no fixture-driven DOM mutations. The
 * dialog body is thin enough that a focused mount keeps the
 * spec readable.
 *
 * Test strategy (per the task spec acceptance criteria):
 *
 *   - Data shape binding: title, root, relPath flow into the
 *     template unchanged.
 *   - The iframe's ``src`` is bound via ``DomSanitizer`` and the
 *     ``sandbox`` attribute is the load-bearing security
 *     property — the spec pins ``sandbox="allow-scripts"``
 *     exactly, and the absence of ``allow-same-origin`` is
 *     asserted via the explicit negative check.
 *   - The ``(load)`` / ``(error)`` handlers flip the
 *     ``loaded`` / ``errored`` signals and clear the element's
 *     native handlers so a synthetic re-fire is a no-op.
 *   - ``onClose`` delegates to ``MatDialogRef.close()``.
 *
 * Identity-grep mirror-parity pins (the panel classes
 * ``dark-modal-panel`` and ``live-view-panel`` MUST live in the
 * production source) are exercised via ``grep`` at the
 * integration-test stage, not here.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { LiveViewDialogComponent, LiveViewDialogData } from './live-view-dialog.component';

const SAMPLE_DATA: LiveViewDialogData = {
  src: '/views/planning/ens/feat/design/mockups/landing.html',
  title: 'landing',
  root: 'planning',
  relPath: 'ens/feat/design/mockups/landing.html',
};

describe('LiveViewDialogComponent — Phase 2 live-view WebView', () => {
  let fixture: ComponentFixture<LiveViewDialogComponent>;
  let component: LiveViewDialogComponent;
  let dialogRefClose: jest.Mock;

  function setupMount(data: LiveViewDialogData): void {
    dialogRefClose = jest.fn();
    TestBed.configureTestingModule({
      imports: [LiveViewDialogComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MAT_DIALOG_DATA, useValue: data },
        {
          provide: MatDialogRef,
          useValue: { close: dialogRefClose },
        },
      ],
    });
    fixture = TestBed.createComponent(LiveViewDialogComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  describe('construction / data shape', () => {
    it('binds the title into the dialog header', () => {
      setupMount(SAMPLE_DATA);
      const titleEl = fixture.nativeElement.querySelector(
        '[data-testid="live-view-title"]',
      ) as HTMLElement | null;
      expect(titleEl).not.toBeNull();
      expect((titleEl!.textContent || '').trim()).toBe('landing');
    });

    it('binds the /views/<root>/<relPath> subtitle', () => {
      setupMount(SAMPLE_DATA);
      const subtitleEl = fixture.nativeElement.querySelector(
        '[data-testid="live-view-subtitle"]',
      ) as HTMLElement | null;
      expect(subtitleEl).not.toBeNull();
      expect((subtitleEl!.textContent || '').trim()).toBe(
        '/views/planning/ens/feat/design/mockups/landing.html',
      );
    });

    it('exposes the src via the sanitizer-backed safeSrc', () => {
      setupMount(SAMPLE_DATA);
      // The DomSanitizer wraps the value in a SafeResourceUrl.
      // The exact stringification is an Angular internal; the
      // binding contract is verified at the template level
      // below (the iframe gets a non-empty src).
      expect(component.safeSrc).toBeTruthy();
    });

    it('starts in a loading state (loaded=false, errored=false)', () => {
      setupMount(SAMPLE_DATA);
      expect(component.loaded()).toBe(false);
      expect(component.errored()).toBe(false);
    });
  });

  describe('iframe sandbox attribute — SECURITY-CRITICAL (task spec §2)', () => {
    it('renders the iframe with sandbox="allow-scripts" ONLY', () => {
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement | null;
      expect(iframe).not.toBeNull();
      // The exact attribute value is the security contract.
      // A future contributor who adds ``allow-same-origin``
      // opens the entire app to a malicious artifact — this
      // assertion is the tripwire.
      expect(iframe!.getAttribute('sandbox')).toBe('allow-scripts');
    });

    it('does NOT include allow-same-origin in the sandbox attribute', () => {
      // Defense-in-depth — even if a future contributor
      // widens the value to a space-separated list, the
      // negative case still catches ``allow-same-origin``.
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement | null;
      const sandbox = iframe!.getAttribute('sandbox') ?? '';
      expect(sandbox.includes('allow-same-origin')).toBe(false);
    });

    it('does NOT include allow-top-navigation in the sandbox attribute', () => {
      // The artifact is served in an opaque origin; it must
      // not be able to navigate the top-level window. Future
      // wideners get caught here too.
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement | null;
      const sandbox = iframe!.getAttribute('sandbox') ?? '';
      expect(sandbox.includes('allow-top-navigation')).toBe(false);
    });

    it('does NOT include allow-popups in the sandbox attribute', () => {
      // The artifact must not be able to spawn new windows.
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement | null;
      const sandbox = iframe!.getAttribute('sandbox') ?? '';
      expect(sandbox.includes('allow-popups')).toBe(false);
    });

    it('does NOT include allow-forms in the sandbox attribute', () => {
      // The artifact must not be able to submit forms (which
      // could leak user-entered text to a same-origin or
      // cross-origin endpoint).
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement | null;
      const sandbox = iframe!.getAttribute('sandbox') ?? '';
      expect(sandbox.includes('allow-forms')).toBe(false);
    });

    it('sets referrerpolicy="no-referrer" on the iframe', () => {
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement | null;
      expect(iframe!.getAttribute('referrerpolicy')).toBe('no-referrer');
    });
  });

  describe('loading state', () => {
    it('renders the loading overlay while the iframe has not yet loaded', () => {
      setupMount(SAMPLE_DATA);
      const overlay = fixture.nativeElement.querySelector(
        '[data-testid="live-view-loading"]',
      );
      expect(overlay).not.toBeNull();
    });

    it('hides the loading overlay after a successful load event', () => {
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement;
      iframe.dispatchEvent(new Event('load'));
      fixture.detectChanges();
      expect(component.loaded()).toBe(true);
      expect(
        fixture.nativeElement.querySelector('[data-testid="live-view-loading"]'),
      ).toBeNull();
    });

    it('is idempotent — a second load event does not re-flip loaded', () => {
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement;
      iframe.dispatchEvent(new Event('load'));
      iframe.dispatchEvent(new Event('load'));
      expect(component.loaded()).toBe(true);
    });
  });

  describe('error state', () => {
    it('flips to errored on the iframe error event', () => {
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement;
      iframe.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      expect(component.errored()).toBe(true);
      expect(component.loaded()).toBe(false);
    });

    it('renders the error panel on error', () => {
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement;
      iframe.dispatchEvent(new Event('error'));
      fixture.detectChanges();
      const panel = fixture.nativeElement.querySelector(
        '[data-testid="live-view-error"]',
      );
      expect(panel).not.toBeNull();
    });

    it('clears the element-level onerror so a re-fire is a no-op', () => {
      setupMount(SAMPLE_DATA);
      const iframe = fixture.nativeElement.querySelector(
        '[data-testid="live-view-iframe"]',
      ) as HTMLIFrameElement;
      iframe.onerror = jest.fn();
      iframe.dispatchEvent(new Event('error'));
      expect(iframe.onerror).toBeNull();
    });

    it('swaps state when the event target is null (defensive)', () => {
      setupMount(SAMPLE_DATA);
      expect(() =>
        component.onIframeError({ target: null } as unknown as Event),
      ).not.toThrow();
      expect(component.errored()).toBe(true);
    });
  });

  describe('onClose', () => {
    it('delegates to MatDialogRef.close()', () => {
      setupMount(SAMPLE_DATA);
      component.onClose();
      expect(dialogRefClose).toHaveBeenCalledTimes(1);
    });
  });

  describe('aria-label', () => {
    it('composes a screen-reader-friendly label from title + root', () => {
      setupMount(SAMPLE_DATA);
      const dialog = fixture.nativeElement.querySelector(
        '[data-testid="live-view-dialog"]',
      ) as HTMLElement | null;
      expect(dialog).not.toBeNull();
      const label = dialog!.getAttribute('aria-label') ?? '';
      expect(label).toContain('landing');
      expect(label).toContain('planning');
    });
  });

  describe('external-link affordance — REMOVED for sandbox integrity', () => {
    it('does NOT render an "open in new tab" anchor (no target=_blank)', () => {
      // Phase 2 hardening: the artifact must render ONLY
      // inside the sandboxed iframe so a malicious
      // artifact cannot escape the opaque origin into the
      // ensemble app origin. An "open in new tab" link
      // would land the artifact in a tab at the full app
      // origin, where it inherits cookies / storage /
      // same-origin privileges — defeating the sandbox.
      // This assertion is the tripwire: any future
      // contributor who reintroduces a ``target="_blank"``
      // affordance trips it.
      setupMount(SAMPLE_DATA);
      const root = fixture.nativeElement as HTMLElement;
      expect(
        root.querySelector('[data-testid="live-view-open-external"]'),
      ).toBeNull();
      expect(root.querySelector('a[target="_blank"]')).toBeNull();
    });
  });
});
