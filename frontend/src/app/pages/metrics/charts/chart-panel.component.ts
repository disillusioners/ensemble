import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  input,
} from '@angular/core';

/**
 * The Chart.js PANEL WRAPPER — THE CONTRACT (T1 phase 1, task 1.7).
 *
 * This file is a CONTRACT, not an implementation: Phase 4 implements the
 * four clauses below against the real vendor. The scaffold exists so the
 * contract compiles, so the shape is pinned, and so the wrapper's home is
 * fixed at the canonical path.
 *
 * ── THE FOUR CONTRACT CLAUSES ─────────────────────────────────────────
 *
 * 1. **The per-series DOM proxy.** Every rendered series carries a DOM
 *    proxy element that preserves the `data-series` and `data-render-mode`
 *    hooks. The canvas is not inspectable — the proxy is the stable
 *    handle the e2e gate and the DOM assertions read, and it is what
 *    makes the per-series identity assertable without pixel work.
 *
 * 2. **The visually-hidden data table.** The canvas removes the a11y
 *    surface and the live DOM-assertion gate. The wrapper therefore
 *    renders the SAME samples as a visually-hidden `<table>` — screen
 *    readers get the values, and a DOM assertion can read them back.
 *
 * 3. **`chart.destroy()` in `ngOnDestroy`.** The shell destroys the tab
 *    bodies via `NgComponentOutlet`; a missing destroy leaks a canvas +
 *    a ResizeObserver PER TAB SWITCH. The destroy is therefore
 *    mandatory, not a tidy-up.
 *
 * 4. **The zoneless rule.** The app is ZONELESS (`zone.js` is declared
 *    but never loaded). Therefore NO Angular-rendered overlay may depend
 *    on a Chart.js redraw: an overlay that is positioned from the canvas
 *    would need the canvas to repaint before Angular can re-render it,
 *    and nothing schedules that repaint. The overlay is rendered from
 *    the panel's own state, never from a Chart.js redraw.
 *
 * ── THE IMPORT SHAPE (AM-15 + C-1) ────────────────────────────────────
 *
 * The vendor arrives through the DYNAMIC `await import('chart.js')` form
 * and NEVER through a static import statement — the static form
 * (`from 'chart.js'`) is FORBIDDEN here and everywhere else in the tree.
 *
 * Why the static form is forbidden even inside this lazily-loaded page:
 * a static import inside a lazily-loaded tree is extracted into a
 * LAZY-ONLY sibling chunk, so it satisfies the initial-total budget AND
 * the initial-chunk marker scan AND a location-only rule — the leak is
 * invisible to every one of them. Only the import SHAPE (and the route
 * fetch set) catches it. The dynamic form puts the vendor in its own
 * chunk, loaded on demand.
 *
 * The wrapper's home is `src/app/pages/metrics/**` — the import-site rule
 * (the pins spec's positive pin) is satisfied by construction, because
 * the rule REQUIRES every `chart.js` import site to live under this tree.
 */

/** One series of a panel — the shape the wrapper renders. */
export interface ChartPanelSeries {
  /** The stable identity of the series (the `data-series` hook). */
  readonly id: string;
  /** The human label of the series. */
  readonly label: string;
  /** The render mode — `step` for a counter, `line` for a gauge. */
  readonly mode: 'step' | 'line';
  /** The samples — epoch-ms `t`, numeric `v`. */
  readonly points: readonly { readonly t: number; readonly v: number }[];
}

/** The minimal Chart.js instance surface the wrapper holds. */
interface PanelChart {
  destroy(): void;
}

@Component({
  selector: 'app-metrics-chart-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="chart-panel" data-test="chart-panel">
      @for (series of series(); track series.id) {
        <div
          class="chart-panel-proxy"
          [attr.data-series]="series.id"
          [attr.data-render-mode]="series.mode"
          data-test="chart-panel-proxy"
        ></div>
      }

      <canvas class="chart-panel-canvas" data-test="chart-panel-canvas"></canvas>

      <table class="chart-panel-data visually-hidden" data-test="chart-panel-data">
        <caption class="visually-hidden">{{ label() }}</caption>
        <thead>
          <tr>
            <th scope="col">time</th>
            <th scope="col">value</th>
          </tr>
        </thead>
        <tbody>
          @for (series of series(); track series.id) {
            @for (point of series.points; track point.t) {
              <tr [attr.data-series]="series.id">
                <td>{{ point.t }}</td>
                <td>{{ point.v }}</td>
              </tr>
            }
          }
        </tbody>
      </table>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .chart-panel {
        position: relative;
      }

      .chart-panel-proxy {
        display: none;
      }

      .visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        margin: -1px;
        padding: 0;
        overflow: hidden;
        clip: rect(0 0 0 0);
        white-space: nowrap;
        border: 0;
      }
    `,
  ],
})
export class ChartPanelComponent implements OnDestroy {
  /** The series to render — the panel is one metric-kind + unit group. */
  readonly series = input<readonly ChartPanelSeries[]>([]);

  /** The panel's accessible label. */
  readonly label = input('metrics panel');

  /**
   * The mounted chart — null until Phase 4 mounts one.
   *
   * The shell destroys the tab bodies via `NgComponentOutlet`; a missing
   * destroy leaks a canvas + a ResizeObserver PER TAB SWITCH. The
   * lifecycle hook below is therefore part of the CONTRACT, not a
   * tidy-up.
   */
  private chart: PanelChart | null = null;

  ngOnDestroy(): void {
    this.chart?.destroy();
    this.chart = null;
  }

  /**
   * The dynamic vendor load — the ONLY sanctioned import shape.
   *
   * The static form is FORBIDDEN (see the file header): a static import
   * inside this lazily-loaded tree lands in a lazy-only sibling chunk,
   * where the initial-total budget and the initial-chunk marker scan
   * cannot see it.
   */
  protected async loadChartConstructor(): Promise<unknown> {
    const mod = await import('chart.js');
    return mod.Chart;
  }
}
