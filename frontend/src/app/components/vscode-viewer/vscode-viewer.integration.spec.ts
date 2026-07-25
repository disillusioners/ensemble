import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Component, signal } from '@angular/core';
import { By } from '@angular/platform-browser';
import { VsCodeViewerComponent } from './vscode-viewer.component';

/**
 * Lightweight host that mirrors the workspace template's binding shape:
 * `<app-vscode-viewer [projectId]="…" [workdir]="…" />`. Reused from the
 * unit spec so the integration tests can drive the component through the
 * same signal-input surface the real workspace uses.
 */
@Component({
  standalone: true,
  imports: [VsCodeViewerComponent],
  template: `
    <app-vscode-viewer
      [projectId]="projectId()"
      [workdir]="workdir()"
    ></app-vscode-viewer>
  `,
})
class VsCodeViewerHostComponent {
  projectId = signal<string>('');
  workdir = signal<string>('');
}

/**
 * Integration tests for `VsCodeViewerComponent`.
 *
 * The unit spec (`vscode-viewer.component.spec.ts`) covers the debounced
 * `openFolder` path, the computed `iframeUrl`, sandbox tokens, and the
 * `ngOnDestroy` timer cleanup. These integration tests deliberately target
 * the **interaction seams** that the unit tests do NOT exercise:
 *
 * 1. `onIframeLoad()` — the iframe `load` event handler calls
 *    `openFolder(this.workdir())` DIRECTLY (not through the debounce).
 *    The unit spec only drives `openFolder` via the workdir `effect()` +
 *    debounce. The load-handler path — its origin correctness, its empty
 *    guard, and the `loading` flip — is uncovered.
 *
 * 2. Debounce reset on rapid changes — the constructor `effect` clears
 *    and re-arms the timer on every workdir value. The unit spec proves
 *    "fires once after debounce" but not "rapid successive changes send
 *    only the LAST value" (the reset behaviour).
 *
 * 3. Load-event + debounce coexistence — both paths must post with the
 *    same correct `targetOrigin` and must not double-fire for the same
 *    value in a way that corrupts the origin.
 */
describe('VsCodeViewerComponent (integration)', () => {
  let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
  let host: VsCodeViewerHostComponent;

  /** Resolve the rendered <iframe> element from the host fixture. */
  function getIframe(): HTMLIFrameElement {
    return hostFixture.debugElement.query(By.css('iframe'))
      .nativeElement as HTMLIFrameElement;
  }

  /** Resolve the actual viewer component instance. */
  function getViewer(): VsCodeViewerComponent {
    return hostFixture.debugElement.query(
      By.directive(VsCodeViewerComponent),
    ).componentInstance as VsCodeViewerComponent;
  }

  /**
   * Install a postMessage spy on the iframe's `contentWindow`. Returns the
   * spy so each test can assert on the recorded calls. The property is
   * `configurable` so multiple installs within one test are safe.
   */
  function spyOnContentWindow(): jest.Mock {
    const iframe = getIframe();
    const postMessageSpy = jest.fn();
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: { postMessage: postMessageSpy },
    });
    return postMessageSpy;
  }

  beforeEach(async () => {
    jest.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [VsCodeViewerHostComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
    }).compileComponents();

    hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
    host = hostFixture.componentInstance;
    hostFixture.detectChanges();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  // ── onIframeLoad: the load-event path is uncovered by the unit spec ──

  describe('onIframeLoad (load-event path)', () => {
    it('posts openFolder immediately via the load handler, bypassing the debounce', () => {
      const spy = spyOnContentWindow();
      const workdir = '/Users/test/projects/foo';
      host.projectId.set('real-project');
      host.workdir.set(workdir);
      hostFixture.detectChanges();

      // Do NOT advance the 300ms debounce. The load handler must post
      // immediately, proving it is independent of the effect+timer.
      expect(spy).not.toHaveBeenCalled();

      getViewer().onIframeLoad();

      expect(spy).toHaveBeenCalledTimes(1);
      const [payload] = spy.mock.calls[0];
      expect(payload).toEqual(
        expect.objectContaining({ type: 'openFolder', path: workdir }),
      );
    });

    it('uses window.location.origin (absolute URL) as targetOrigin, never a relative path', () => {
      const spy = spyOnContentWindow();
      host.projectId.set('real-project');
      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      getViewer().onIframeLoad();

      expect(spy).toHaveBeenCalledTimes(1);
      const targetOrigin = spy.mock.calls[0][1];
      // C3 — must be the absolute origin. A relative string like
      // '/vscode/' or a bare host would be silently dropped by browsers.
      expect(targetOrigin).toBe(window.location.origin);
      expect(targetOrigin).toMatch(/^https?:\/\//);
      expect(targetOrigin).not.toBe('/vscode/');
      expect(targetOrigin).not.toBe('*');
    });

    it('flips the loading signal to false when the iframe loads', () => {
      const viewer = getViewer();
      // The component starts in the loading state.
      expect(viewer.loading()).toBe(true);

      getViewer().onIframeLoad();

      expect(viewer.loading()).toBe(false);
    });

    it('does NOT post when workdir is empty (load-handler guard)', () => {
      const spy = spyOnContentWindow();
      // Empty workdir — the guard inside openFolder must short-circuit.
      host.projectId.set('real-project');
      host.workdir.set('');
      hostFixture.detectChanges();

      expect(() => getViewer().onIframeLoad()).not.toThrow();
      expect(spy).not.toHaveBeenCalled();
    });
  });

  // ── Debounce reset: rapid changes send only the last value ──────────

  describe('debounce reset on rapid workdir changes', () => {
    it('posts only the LAST value when workdir changes rapidly, exactly once', () => {
      const spy = spyOnContentWindow();
      host.projectId.set('real-project');
      hostFixture.detectChanges();

      // Three rapid successive changes without advancing the clock. The
      // effect clears + re-arms the timer on each, so only the final
      // value should be posted once the debounce elapses.
      host.workdir.set('/path/a');
      hostFixture.detectChanges();
      host.workdir.set('/path/b');
      hostFixture.detectChanges();
      host.workdir.set('/path/c');
      hostFixture.detectChanges();

      // Half-way through — nothing posted yet (timer keeps resetting).
      jest.advanceTimersByTime(150);
      expect(spy).not.toHaveBeenCalled();

      // Cross the 300ms boundary relative to the LAST change.
      jest.advanceTimersByTime(150);

      expect(spy).toHaveBeenCalledTimes(1);
      const [payload] = spy.mock.calls[0];
      expect(payload).toEqual(
        expect.objectContaining({ type: 'openFolder', path: '/path/c' }),
      );
      // The intermediate values must never have been posted.
      const postedPaths = spy.mock.calls.map(
        (c: unknown[]) => (c[0] as { path?: string }).path,
      );
      expect(postedPaths).not.toContain('/path/a');
      expect(postedPaths).not.toContain('/path/b');
    });

    it('does not leak the cleared timer value after a reset (no stale post)', () => {
      const spy = spyOnContentWindow();
      host.projectId.set('real-project');
      host.workdir.set('/path/a');
      hostFixture.detectChanges();

      // Reset to a new value BEFORE the first debounce fires.
      host.workdir.set('/path/b');
      hostFixture.detectChanges();

      jest.advanceTimersByTime(300);

      // Only '/path/b' — the timer scheduled for '/path/a' was cleared.
      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy.mock.calls[0][0]).toEqual(
        expect.objectContaining({ type: 'openFolder', path: '/path/b' }),
      );
    });
  });

  // ── Load-event + debounce coexistence ──────────────────────────────

  describe('load-event and debounce coexistence', () => {
    it('both paths post with the same absolute window.location.origin', () => {
      const spy = spyOnContentWindow();
      host.projectId.set('real-project');
      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      // Fire the load handler (immediate post).
      getViewer().onIframeLoad();

      // Then let the debounce fire (delayed post).
      jest.advanceTimersByTime(300);

      // Two posts total — one from each path — both using the origin.
      expect(spy).toHaveBeenCalledTimes(2);
      for (const call of spy.mock.calls) {
        const targetOrigin = call[1];
        expect(targetOrigin).toBe(window.location.origin);
        expect(targetOrigin).toMatch(/^https?:\/\//);
        expect(targetOrigin).not.toBe('/vscode/');
        expect(targetOrigin).not.toBe('*');
      }
    });
  });
});
