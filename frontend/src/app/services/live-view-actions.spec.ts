/**
 * LiveViewActionsService spec.
 *
 * Pure service tests — no Angular TestBed (the service is
 * `providedIn: 'root'` and depends only on ``MatDialog``; a
 * stub on the dialog ref is enough). The test strategy mirrors
 * the contract documented in
 * ``live-view-actions.service.ts``:
 *
 *  - ``openViewer`` re-runs the URL matcher and refuses to
 *    open a dialog on a URL that does not pass the
 *    ``parseLiveViewUrl`` gate.
 *  - The dialog open-config carries the correct
 *    ``panelClass`` (``dark-modal-panel`` + ``live-view-panel``)
 *    and dimensions (95vw / 90vh).
 *  - The dialog ``data`` payload is derived from the parsed
 *    URL (``src``, ``title``, ``root``, ``relPath``).
 */
import { TestBed } from '@angular/core/testing';
import { MatDialog } from '@angular/material/dialog';
import { LiveViewActionsService } from './live-view-actions.service';

describe('LiveViewActionsService — Phase 2 live-view', () => {
  let service: LiveViewActionsService;
  let dialogOpen: jest.Mock;
  let lastOpenConfig: unknown;

  beforeEach(() => {
    dialogOpen = jest.fn().mockImplementation((_component, config) => {
      lastOpenConfig = config;
      return { close: jest.fn() };
    });
    TestBed.configureTestingModule({
      providers: [{ provide: MatDialog, useValue: { open: dialogOpen } }],
    });
    service = TestBed.inject(LiveViewActionsService);
  });

  describe('openViewer — accepted URLs open the dialog', () => {
    it('opens the dialog for a planning-root URL', () => {
      const ref = service.openViewer(
        '/views/planning/ens/feat/design/mockups/landing.html',
      );
      expect(ref).not.toBeNull();
      expect(dialogOpen).toHaveBeenCalledTimes(1);
    });

    it('opens the dialog for a designer-artifact-root URL', () => {
      service.openViewer(
        '/views/designer-artifact/ens/feat/design/mockups/page.html',
      );
      expect(dialogOpen).toHaveBeenCalledTimes(1);
    });

    it('opens the dialog for a tmp-images-root URL', () => {
      service.openViewer(
        '/views/tmp-images/abcdef0123456789abcdef0123456789',
      );
      expect(dialogOpen).toHaveBeenCalledTimes(1);
    });
  });

  describe('openViewer — rejected URLs do NOT open the dialog (defense in depth)', () => {
    // The chip is only built from a URL the matcher already
    // accepted, so the dialog re-parse is a defense-in-depth
    // layer. The negative cases below MUST all silently drop —
    // a dialog open on a rejected URL would be the entire
    // security premise failing.
    const rejected: ReadonlyArray<{ href: string; reason: string }> = [
      { href: 'http://evil/views/x/y', reason: 'absolute URL' },
      { href: 'https://evil/views/x/y', reason: 'absolute URL https' },
      { href: '//host/views/x/y', reason: 'protocol-relative' },
      { href: '/views/../etc/passwd', reason: 'traversal' },
      { href: '/views/planning/ens/plan.md?foo=bar', reason: 'query string' },
      { href: '/views', reason: 'no trailing path' },
      { href: '/viewsfoo/x/y', reason: 'wrong prefix' },
      { href: 'javascript:alert(1)', reason: 'javascript scheme' },
      { href: '', reason: 'empty' },
    ];

    for (const { href, reason } of rejected) {
      it(`refuses to open for ${JSON.stringify(href)} (${reason})`, () => {
        const ref = service.openViewer(href);
        expect(ref).toBeNull();
        expect(dialogOpen).not.toHaveBeenCalled();
      });
    }
  });

  describe('openViewer — dialog open-config', () => {
    it('passes the dark-modal-panel + live-view-panel classes', () => {
      service.openViewer('/views/planning/ens/plan.md');
      const config = lastOpenConfig as {
        panelClass: string[];
      };
      expect(config.panelClass).toContain('dark-modal-panel');
      expect(config.panelClass).toContain('live-view-panel');
    });

    it('opens at 95vw × 90vh (large, page-mockup-friendly)', () => {
      service.openViewer('/views/planning/ens/plan.md');
      const config = lastOpenConfig as {
        width: string;
        height: string;
        maxWidth: string;
        maxHeight: string;
      };
      expect(config.width).toBe('95vw');
      expect(config.height).toBe('90vh');
      expect(config.maxWidth).toBe('95vw');
      expect(config.maxHeight).toBe('90vh');
    });

    it('keeps disableClose=false so Escape + backdrop both close', () => {
      service.openViewer('/views/planning/ens/plan.md');
      const config = lastOpenConfig as { disableClose: boolean };
      expect(config.disableClose).toBe(false);
    });

    it('autoFocuses the first tabbable element (the close button)', () => {
      service.openViewer('/views/planning/ens/plan.md');
      const config = lastOpenConfig as { autoFocus: string };
      expect(config.autoFocus).toBe('first-tabbable');
    });
  });

  describe('openViewer — data payload derived from the parsed URL', () => {
    it('passes the original href as data.src', () => {
      service.openViewer(
        '/views/planning/ens/feat/design/mockups/landing.html',
      );
      const config = lastOpenConfig as { data: { src: string } };
      expect(config.data.src).toBe(
        '/views/planning/ens/feat/design/mockups/landing.html',
      );
    });

    it('derives the chip title by stripping the file extension', () => {
      service.openViewer(
        '/views/planning/ens/feat/design/mockups/landing.html',
      );
      const config = lastOpenConfig as { data: { title: string } };
      expect(config.data.title).toBe('landing');
    });

    it('passes the root name as data.root', () => {
      service.openViewer(
        '/views/designer-artifact/ens/feat/design/mockups/page.html',
      );
      const config = lastOpenConfig as { data: { root: string } };
      expect(config.data.root).toBe('designer-artifact');
    });

    it('passes the path-under-root as data.relPath', () => {
      service.openViewer(
        '/views/planning/ens/feat/design/mockups/landing.html',
      );
      const config = lastOpenConfig as { data: { relPath: string } };
      expect(config.data.relPath).toBe(
        'ens/feat/design/mockups/landing.html',
      );
    });
  });
});
