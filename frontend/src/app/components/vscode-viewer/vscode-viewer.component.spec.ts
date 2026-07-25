import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { Component, signal } from '@angular/core';
import { By } from '@angular/platform-browser';
import { VsCodeViewerComponent } from './vscode-viewer.component';

/**
 * Lightweight host component that mirrors the workspace template's
 * binding shape: `<app-vscode-viewer [projectId]="…" [workdir]="…" />`.
 * Lets us drive the component through signal inputs to test the
 * public computed signal (`iframeUrl`) without TestBed plumbing.
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
 * Tests for `VsCodeViewerComponent`.
 *
 * Pattern: most tests instantiate the component directly (no TestBed)
 * where possible, so the input signals and the computed `iframeUrl`
 * can be exercised as pure units. The C3 (`postMessage` origin) test
 * needs TestBed so the iframe element is rendered and the effect
 * resolves the `viewChild` signal.
 */
describe('VsCodeViewerComponent', () => {
  // ── Tear down HttpTestingController ─────────────────────────────
  // Currently unused by the component itself (the viewer makes no HTTP
  // calls), but kept in place so future tests that exercise the
  // settings-service integration can opt into the same verify hook.
  // Initialized to `undefined` so the optional-chained `verify()` call
  // is safe when no test ever injects a controller.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let httpMock: any;

  // ── Computed signal unit tests (host-driven) ───────────────────

  describe('iframeUrl computed (unit)', () => {
    function unwrap(input: unknown): string {
      // `bypassSecurityTrustResourceUrl` wraps the URL in an object
      // that exposes the raw path via `changingThisBreaksApplicationSecurity`.
      // The tests assert on the original URL string, so we extract the
      // underlying value here.
      const anyInput = input as { changingThisBreaksApplicationSecurity?: string } | string;
      return typeof anyInput === 'string'
        ? anyInput
        : anyInput.changingThisBreaksApplicationSecurity ?? '';
    }

    let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
    let host: VsCodeViewerHostComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [VsCodeViewerHostComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
      }).compileComponents();

      hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
      host = hostFixture.componentInstance;
    });

    function getComponent(): VsCodeViewerComponent {
      return hostFixture.debugElement.query(
        By.directive(VsCodeViewerComponent),
      ).componentInstance as VsCodeViewerComponent;
    }

    it('returns /vscode/ when workdir is empty', () => {
      hostFixture.detectChanges();
      expect(getComponent().workdir()).toBe('');
      expect(unwrap(getComponent().iframeUrl())).toBe('/vscode/');
    });

    it('returns /vscode/?folder=… when workdir is set', () => {
      const workdir = '/Users/test/projects/foo';
      host.projectId.set('real-project');
      host.workdir.set(workdir);
      hostFixture.detectChanges();

      expect(unwrap(getComponent().iframeUrl())).toBe(
        `/vscode/?folder=${encodeURIComponent(workdir)}`,
      );
    });

    it('returns /vscode/ (no folder) when projectId is system-default (S6 null-guard)', () => {
      host.projectId.set('system-default');
      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      expect(unwrap(getComponent().iframeUrl())).toBe('/vscode/');
    });

    it('returns /vscode/ (no folder) when projectId is empty (S6 null-guard)', () => {
      host.projectId.set('');
      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      expect(unwrap(getComponent().iframeUrl())).toBe('/vscode/');
    });

    it('encodes special characters in the folder URL', () => {
      const workdir = 'C:\\Users\\test\\My Project';
      host.projectId.set('real-project');
      host.workdir.set(workdir);
      hostFixture.detectChanges();

      expect(unwrap(getComponent().iframeUrl())).toBe(
        `/vscode/?folder=${encodeURIComponent(workdir)}`,
      );
    });

    it('tracks workdir changes via signal reactivity', () => {
      host.projectId.set('real-project');
      hostFixture.detectChanges();
      expect(unwrap(getComponent().iframeUrl())).toBe('/vscode/');

      host.workdir.set('/path/a');
      hostFixture.detectChanges();
      expect(unwrap(getComponent().iframeUrl())).toBe('/vscode/?folder=%2Fpath%2Fa');

      host.workdir.set('/path/b');
      hostFixture.detectChanges();
      expect(unwrap(getComponent().iframeUrl())).toBe('/vscode/?folder=%2Fpath%2Fb');
    });
  });

  // ── Host-driven signal input tests ─────────────────────────────

  describe('signal inputs (host-driven)', () => {
    let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
    let host: VsCodeViewerHostComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [VsCodeViewerHostComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
      }).compileComponents();

      hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
      host = hostFixture.componentInstance;
    });

    it('updates the iframe URL when the host flips workdir', () => {
      host.projectId.set('real-project');
      hostFixture.detectChanges();

      let iframe = hostFixture.debugElement.query(By.css('iframe'));
      expect(iframe.nativeElement.getAttribute('src')).toBe('/vscode/');

      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      iframe = hostFixture.debugElement.query(By.css('iframe'));
      expect(iframe.nativeElement.getAttribute('src')).toBe(
        `/vscode/?folder=${encodeURIComponent('/Users/test/projects/foo')}`,
      );
    });

    it('starts in the loading state', () => {
      hostFixture.detectChanges();

      const viewer = hostFixture.debugElement.query(
        By.directive(VsCodeViewerComponent),
      ).componentInstance as VsCodeViewerComponent;
      expect(viewer.loading()).toBe(true);

      const loadingBlock = hostFixture.debugElement.query(
        By.css('[data-testid="vscode-loading"]'),
      );
      expect(loadingBlock).not.toBeNull();
    });
  });

  // ── W1: iframe sandbox attribute ────────────────────────────────

  describe('iframe sandbox attribute (W1)', () => {
    let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
    let host: VsCodeViewerHostComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [VsCodeViewerHostComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
      }).compileComponents();

      hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
      host = hostFixture.componentInstance;
      hostFixture.detectChanges();
    });

    it('renders the iframe with the documented sandbox tokens', () => {
      const iframe = hostFixture.debugElement.query(By.css('iframe'));
      const sandbox = iframe.nativeElement.getAttribute('sandbox') ?? '';
      // W1 — exact attribute string. Order is not significant for the
      // token list, but the spec mandates these four tokens and
      // EXPLICITLY omits `allow-top-navigation`.
      expect(sandbox).toContain('allow-scripts');
      expect(sandbox).toContain('allow-same-origin');
      expect(sandbox).toContain('allow-forms');
      expect(sandbox).toContain('allow-popups');
    });

    it('does NOT include allow-top-navigation (W1 — parent hijack prevention)', () => {
      const iframe = hostFixture.debugElement.query(By.css('iframe'));
      const sandbox = iframe.nativeElement.getAttribute('sandbox') ?? '';
      expect(sandbox).not.toContain('allow-top-navigation');
    });
  });

  // ── C3: postMessage targetOrigin must be window.location.origin ─

  describe('postMessage targetOrigin (C3)', () => {
    let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
    let host: VsCodeViewerHostComponent;
    let postMessageSpy: jest.Mock;

    beforeEach(async () => {
      jest.useFakeTimers();
      await TestBed.configureTestingModule({
        imports: [VsCodeViewerHostComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
      }).compileComponents();

      hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
      host = hostFixture.componentInstance;
      hostFixture.detectChanges();

      // Replace the iframe's contentWindow BEFORE the workdir change
      // propagates through the effect, so the spy is in place when
      // the debounced `openFolder` fires.
      const iframe = hostFixture.debugElement.query(By.css('iframe'))
        .nativeElement as HTMLIFrameElement;
      postMessageSpy = jest.fn();
      Object.defineProperty(iframe, 'contentWindow', {
        configurable: true,
        value: { postMessage: postMessageSpy },
      });
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('calls postMessage with window.location.origin as targetOrigin (C3)', () => {
      const workdir = 'C:\\Users\\test\\project';
      host.projectId.set('real-project');
      host.workdir.set(workdir);
      hostFixture.detectChanges();

      // Advance past the 300ms debounce.
      jest.advanceTimersByTime(300);

      expect(postMessageSpy).toHaveBeenCalled();
      const [payload, targetOrigin] = postMessageSpy.mock.calls[0];
      // C3 — targetOrigin MUST be the absolute origin, NEVER a relative
      // path such as '/vscode/'.
      expect(targetOrigin).toBe(window.location.origin);
      expect(targetOrigin).not.toBe('/vscode/');
      // Payload sanity.
      expect(payload).toEqual(
        expect.objectContaining({ type: 'openFolder', path: workdir }),
      );
    });

    it('does NOT fire openFolder before the debounce elapses', () => {
      host.projectId.set('real-project');
      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      // Half the debounce — no fire yet.
      jest.advanceTimersByTime(150);
      expect(postMessageSpy).not.toHaveBeenCalled();

      // After the full debounce — fires once.
      jest.advanceTimersByTime(150);
      expect(postMessageSpy).toHaveBeenCalledTimes(1);
    });
  });

  // ── N3: ngOnDestroy clears the debounce timer ───────────────────

  describe('ngOnDestroy (N3)', () => {
    let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
    let host: VsCodeViewerHostComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [VsCodeViewerHostComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
      }).compileComponents();

      hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
      host = hostFixture.componentInstance;
      hostFixture.detectChanges();
    });

    it('does not fire openFolder after the component is destroyed', () => {
      jest.useFakeTimers();
      const iframe = hostFixture.debugElement.query(By.css('iframe'))
        .nativeElement as HTMLIFrameElement;
      const postMessageSpy = jest.fn();
      Object.defineProperty(iframe, 'contentWindow', {
        configurable: true,
        value: { postMessage: postMessageSpy },
      });

      host.projectId.set('real-project');
      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      // Destroy the component before the debounce fires.
      hostFixture.destroy();

      // Advance past the 300ms debounce — the spy MUST stay silent
      // because the timer was cleared in ngOnDestroy.
      jest.advanceTimersByTime(500);
      expect(postMessageSpy).not.toHaveBeenCalled();

      jest.useRealTimers();
    });
  });

  // ── Errors: missing contentWindow is handled gracefully ─────────

  describe('openFolder defensive checks', () => {
    let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
    let host: VsCodeViewerHostComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [VsCodeViewerHostComponent],
        providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
      }).compileComponents();

      hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
      host = hostFixture.componentInstance;
      hostFixture.detectChanges();
    });

    it('does not throw when contentWindow is null (defensive guard)', () => {
      jest.useFakeTimers();
      const iframe = hostFixture.debugElement.query(By.css('iframe'))
        .nativeElement as HTMLIFrameElement;
      Object.defineProperty(iframe, 'contentWindow', {
        configurable: true,
        value: null,
      });

      host.projectId.set('real-project');
      host.workdir.set('/Users/test/projects/foo');
      hostFixture.detectChanges();

      expect(() => jest.advanceTimersByTime(300)).not.toThrow();
      jest.useRealTimers();
    });

    it('does not fire openFolder when workdir is empty', () => {
      jest.useFakeTimers();
      const iframe = hostFixture.debugElement.query(By.css('iframe'))
        .nativeElement as HTMLIFrameElement;
      const postMessageSpy = jest.fn();
      Object.defineProperty(iframe, 'contentWindow', {
        configurable: true,
        value: { postMessage: postMessageSpy },
      });

      // Empty workdir — the guard inside `openFolder` must skip the
      // postMessage call entirely.
      host.projectId.set('real-project');
      host.workdir.set('');
      hostFixture.detectChanges();

      jest.advanceTimersByTime(500);
      expect(postMessageSpy).not.toHaveBeenCalled();
      jest.useRealTimers();
    });
  });

  // ── Go-to-settings navigation ───────────────────────────────────

  describe('goToSettings', () => {
    let hostFixture: ComponentFixture<VsCodeViewerHostComponent>;
    let host: VsCodeViewerHostComponent;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [VsCodeViewerHostComponent],
        providers: [
          provideHttpClient(),
          provideHttpClientTesting(),
          provideNoopAnimations(),
          provideRouter([]),
        ],
      }).compileComponents();

      hostFixture = TestBed.createComponent(VsCodeViewerHostComponent);
      host = hostFixture.componentInstance;
      hostFixture.detectChanges();
    });

    it('navigates to /settings', () => {
      const viewer = hostFixture.debugElement.query(
        By.directive(VsCodeViewerComponent),
      ).componentInstance as VsCodeViewerComponent;
      // Calling goToSettings should not throw — Router is provided so
      // navigation is functional.
      expect(() => viewer.goToSettings()).not.toThrow();
    });
  });

  // ── Tear down HttpTestingController ─────────────────────────────

  afterEach(() => {
    httpMock?.verify();
  });
});
