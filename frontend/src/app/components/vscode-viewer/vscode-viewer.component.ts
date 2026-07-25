import {
  Component,
  ElementRef,
  OnDestroy,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Router } from '@angular/router';

/**
 * VS Code Server iframe wrapper.
 *
 * Renders the code-server web UI inside a sandboxed iframe. The iframe
 * URL is `/vscode/` (proxied by the FastAPI backend so the iframe is
 * same-origin) — the validated folder path is forwarded via the
 * `?folder=` query parameter and, as a best-effort enhancement, via
 * `postMessage` with `{ type: 'openFolder', path }` once the iframe
 * finishes loading.
 *
 * Constraints:
 *
 * - **C2**: the `workdir` input arrives from the dedicated
 *   `/api/projects/{id}/vscode-folder` endpoint, never from the raw
 *   `project.main_directory` field. The backend enforces path
 *   containment server-side.
 *
 * - **C3**: `postMessage` uses `window.location.origin` (an absolute
 *   URL) as the `targetOrigin`. Per the HTML spec the value must be
 *   an absolute URL; relative paths are silently dropped by the
 *   browser.
 *
 * - **W1**: the iframe carries a `sandbox` attribute restricting what
 *   the embedded document can do. `allow-top-navigation` is
 *   intentionally omitted so the embedded page cannot navigate the
 *   parent window.
 *
 * - **S6**: `projectId` and `workdir` are signal inputs (`input()`)
 *   rather than `@Input()` decorator properties, so the `effect()`
 *   that opens the folder reacts automatically without needing
 *   `ngOnChanges`.
 *
 * - **N3**: the debounce timer is cleared in `ngOnDestroy()` so a
 *   delayed `openFolder()` cannot fire after the component is gone.
 */
@Component({
  selector: 'app-vscode-viewer',
  standalone: true,
  imports: [MatProgressSpinnerModule, MatButtonModule],
  template: `
    <div class="vscode-container">
      @if (loading()) {
        <div class="vscode-loading" data-testid="vscode-loading">
          <mat-spinner diameter="40"></mat-spinner>
          <p>Starting VS Code Server...</p>
        </div>
      }
      @if (error()) {
        <div class="vscode-error" data-testid="vscode-error">
          <p>VS Code Server is not running.</p>
          <button mat-raised-button color="primary" (click)="goToSettings()">
            Configure in Settings
          </button>
        </div>
      }
      <iframe
        #iframe
        [src]="iframeUrl()"
        class="vscode-iframe"
        [class.loaded]="!loading() && !error()"
        (load)="onIframeLoad()"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
        allow="clipboard-read; clipboard-write; fullscreen"
        title="VS Code Server"
      ></iframe>
    </div>
  `,
  styleUrl: './vscode-viewer.component.scss',
})
export class VsCodeViewerComponent implements OnDestroy {
  private readonly router = inject(Router);
  private readonly sanitizer = inject(DomSanitizer);

  // S6 — signal inputs (not @Input). The reactive graph (computed
  // `iframeUrl`, the `effect()` that opens the folder) tracks both
  // inputs automatically.
  /** Project ID this viewer belongs to. */
  readonly projectId = input<string>('');
  /** Validated folder path from `/api/projects/{id}/vscode-folder`. */
  readonly workdir = input<string>('');

  readonly loading = signal(true);
  readonly error = signal(false);
  private readonly iframeRef = viewChild<ElementRef<HTMLIFrameElement>>('iframe');

  /**
   * Debounce handle for the workdir-driven `openFolder` call. N3 — the
   * timer is cleared in `ngOnDestroy()` so a fire-after-destroy does
   * not leak.
   */
  private _reloadTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * URL the iframe loads. `/vscode/` is the proxied code-server root
   * (same-origin via the FastAPI proxy). When a validated folder is
   * available we append `?folder=` so the iframe opens the right
   * directory even before our `postMessage` lands.
   *
   * System-default and empty project IDs never have a real workdir, so
   * the URL stays at the base form.
   *
   * The raw string is wrapped in `bypassSecurityTrustResourceUrl`
   * because Angular's `[src]` binding on `<iframe>` rejects arbitrary
   * URLs as a defence against XSS via crafted `?folder=` values. The
   * server-side endpoint already enforces path containment, so the
   * sanitised string is safe.
   */
  readonly iframeUrl = computed<SafeResourceUrl>(() => {
    const base = '/vscode/';
    const dir = this.workdir();
    const pid = this.projectId();
    // S6 null-guard — system-default or empty project ID never has a
    // real folder to open.
    const url = !dir || pid === 'system-default' || pid === ''
      ? base
      : `${base}?folder=${encodeURIComponent(dir)}`;
    return this.sanitizer.bypassSecurityTrustResourceUrl(url);
  });

  constructor() {
    // S6 — react to workdir changes via effect. Reads the signal
    // unconditionally before any if-branch so the effect always
    // subscribes to it (Angular effect dependency-tracking hazard).
    effect(() => {
      const dir = this.workdir();
      // Reset the debounce on every new value so rapid project
      // switches only fire `openFolder` once after the user settles.
      if (this._reloadTimer) clearTimeout(this._reloadTimer);
      this._reloadTimer = setTimeout(() => {
        if (dir) this.openFolder(dir);
      }, 300);
    });
  }

  /**
   * Iframe `load` handler. Flips the loading state off and forwards
   * the folder one more time via `postMessage` — the iframe may have
   * reloaded itself between the initial render and the bind, so this
   * catches the steady-state ready event.
   */
  onIframeLoad(): void {
    this.loading.set(false);
    this.openFolder(this.workdir());
  }

  /**
   * Send an `openFolder` command to the embedded code-server via
   * `postMessage`. C3 — `targetOrigin` is the absolute URL
   * `window.location.origin`; relative paths are silently dropped by
   * browsers.
   */
  private openFolder(path: string): void {
    if (!path) return;
    const iframe = this.iframeRef()?.nativeElement;
    if (!iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage(
      { type: 'openFolder', path },
      window.location.origin,
    );
  }

  /** Navigate to the settings page where the user can start the server. */
  goToSettings(): void {
    this.router.navigate(['/settings']);
  }

  ngOnDestroy(): void {
    // N3 — clear the debounce timer so a delayed `openFolder` cannot
    // fire after the component is gone.
    if (this._reloadTimer) clearTimeout(this._reloadTimer);
  }
}
