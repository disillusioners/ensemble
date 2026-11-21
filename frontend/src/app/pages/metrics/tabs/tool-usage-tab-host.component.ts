import {
  ChangeDetectionStrategy,
  Component,
  ComponentRef,
  OnDestroy,
  OnInit,
  ViewContainerRef,
  inject,
  input,
  signal,
} from '@angular/core';

/**
 * The TOOL-USAGE tab's THIN LAZY HOST (phase4-plan Task 2/4 — the
 * frozen host pattern, architect §6.1; the mount mechanics mirror the
 * `app.ts:391` chat-root VCR precedent).
 *
 * ── WHY THIS COMPONENT EXISTS (the lazy mechanism) ─────────────────────
 *
 * The shell's exact-cover map (`METRICS_TAB_COMPONENTS: Readonly<Record<
 * MetricsTabId, Type<unknown>>>`) demands a STATICALLY-imported `Type`
 * per tab — a missing/extra key is a COMPILE error. The map value for
 * `tool-usage` is THIS thin host; the heavy body
 * (`tool-usage-tab.component`), the service and the models are reached
 * ONLY via the single `await import('./tool-usage-tab.component')`
 * below, so the compiler emits them as a SEPARATE chunk that is fetched
 * on tab selection — NOT in the `/metrics` route fetch set (B4b; the
 * heavy bytes contribute ZERO to the route fetch set).
 *
 * ── THE FIVE SHELL INPUTS (all declared — undeclared inputs THROW) ─────
 *
 * The shell passes the same five inputs to every tab. This host
 * declares them and forwards them ONTO the created heavy ref via
 * `setInput`. The forwarded values are a MOUNT-TIME snapshot by design:
 * while a custom tab is active the frame's window is snapped-and-disabled,
 * the baseline is disabled (AM-14), and the frame never writes a custom
 * slot's state (AM-1) — the five inputs cannot change under a mounted
 * body, and a tab switch destroys the host wholesale (mat-tab), so the
 * next selection re-mounts with fresh values.
 *
 * ── NEVER BLANK ────────────────────────────────────────────────────────
 *
 * The host renders a loading placeholder while the chunk is in flight
 * and a visible chunk-error state with Retry when the dynamic import
 * fails (offline first paint, etc.) — never a blank tab. The auth-off
 * banner is NOT the host's business: the auth posture is a per-request
 * outcome, so it renders in the HEAVY body via the shared
 * `app-metrics-door-state` (already in the route chunk via nest-reports
 * — zero new vendor bytes).
 *
 * ── ZONELESS DISCIPLINE ────────────────────────────────────────────────
 *
 * The app is zoneless and the mount runs inside a plain promise
 * callback — no zone tick guarantees a first render of the dynamically
 * created subtree, so the host drives its initial change detection
 * explicitly (`changeDetectorRef.detectChanges()`, the `app.ts:391`
 * note).
 */
@Component({
  selector: 'app-metrics-tool-usage-tab-host',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (loading()) {
      <p data-test="tool-usage-tab-loading">loading…</p>
    }
    @if (chunkError()) {
      <div class="tool-usage-chunk-error" data-test="tool-usage-chunk-error">
        <span>the tool-usage panel failed to load.</span>
        <button type="button" (click)="retry()" data-test="tool-usage-chunk-retry">Retry</button>
      </div>
    }
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .tool-usage-chunk-error button {
        border: 1px solid #fca5a5;
        border-radius: 6px;
        background: transparent;
        color: #fca5a5;
        padding: 0.15rem 0.6rem;
        font-size: 0.8rem;
        cursor: pointer;
      }
    `,
  ],
})
export class ToolUsageTabHostComponent implements OnInit, OnDestroy {
  private readonly vcr = inject(ViewContainerRef);
  private ref: ComponentRef<unknown> | null = null;

  // ALL FIVE shell inputs — forwarded onto the created heavy ref.
  readonly state = input<unknown>(null);
  readonly onState = input<unknown>(null);
  readonly window = input<unknown>('7d');
  readonly baseline = input<unknown>(false);
  readonly onRangeChange = input<unknown>(null);

  // Loading placeholder + visible chunk-error state with retry (never-blank).
  readonly loading = signal(true);
  readonly chunkError = signal<unknown>(null);

  async ngOnInit(): Promise<void> {
    this.loading.set(true);
    this.chunkError.set(null);
    try {
      // The old ref dies FIRST, inside the try — a throw here (or from
      // the dynamic import) is caught and rendered as the chunk-error
      // state instead of rejecting the ngOnInit promise (never blank,
      // never reject).
      this.ref?.destroy();
      this.ref = null;
      const { ToolUsageTabComponent } = await import('./tool-usage-tab.component');
      this.ref = this.vcr.createComponent(ToolUsageTabComponent);
      this.ref.setInput('state', this.state());
      this.ref.setInput('onState', this.onState());
      this.ref.setInput('window', this.window());
      this.ref.setInput('baseline', this.baseline());
      this.ref.setInput('onRangeChange', this.onRangeChange());
      this.ref.changeDetectorRef.detectChanges();
    } catch (e) {
      this.chunkError.set(e);
    } finally {
      this.loading.set(false);
    }
  }

  /** The chunk Retry — re-selection / retry re-runs init; the module cache makes remount near-instant. */
  retry(): void {
    void this.ngOnInit();
  }

  ngOnDestroy(): void {
    this.ref?.destroy();
    this.ref = null;
  }
}
