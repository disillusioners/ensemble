import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  computed,
  effect,
  input,
  signal,
  viewChild,
} from '@angular/core';

import { chartSeriesColor } from '../../../components/charts/chart-theme';
import { chartUtcTicks, formatChartUtcTick, formatChartValue } from '../../../components/charts/house-svg-renderer/house-svg-renderer';
import {
  ChartPointClick,
} from '../../../components/charts/renderer-registry';
import {
  HouseAnomalyMarker,
  HOUSE_ANOMALY_PLUGIN_ID,
  createHouseAnomalyOverlayPlugin,
  houseNearestAnomaly,
} from './anomaly-overlay-plugin';
import {
  HOUSE_DATA_END_PLUGIN_ID,
  HouseDataEnd,
  createHouseDataEndOverlayPlugin,
} from './data-end-overlay-plugin';
import { createHouseCrosshairPlugin } from './crosshair-plugin';
import { METRICS_UNIT_UNKNOWN } from '../metrics-units';

/**
 * The Chart.js PANEL WRAPPER — the interactive-panel role (T1 phase 4,
 * task 4.4 / 4.6).
 *
 * This file was the CONTRACT (phase 1, task 1.7); phase 4 implements
 * the four clauses below against the real vendor.
 *
 * ── THE FOUR CONTRACT CLAUSES ───────────────────────────────────────────
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
 * 4. **The ZONELESS rule.** The app is ZONELESS (`zone.js` is declared
 *    but never loaded). Therefore NO Angular-rendered overlay may depend
 *    on a Chart.js redraw: an overlay that is positioned from the canvas
 *    would need the canvas to repaint before Angular can re-render it,
 *    and nothing schedules that repaint. The overlay is rendered from
 *    the panel's own state, never from a Chart.js redraw.
 *
 * ── THE IMPORT SHAPE (AM-15 + C-1) ───────────────────────────────────────
 *
 * The vendor arrives through the DYNAMIC `await import('chart.js')` form
 * and NEVER through a static import statement — the static form
 * (`from 'chart.js'`) is FORBIDDEN here and everywhere else in the tree.
 *
 * Why the static form is forbidden even inside this lazily-loaded page:
 * a static import inside a lazily-loaded tree is extracted into a
 * LAZY-ONLY sibling chunk, so it satisfies the initial-total delta, the
 * initial-chunk marker scan and a location-only rule, because the vendor
 * is extracted into a lazy-only sibling chunk. Only the import SHAPE
 * (and the route fetch set) catches it. The dynamic form puts the vendor
 * in its own chunk, loaded on demand.
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

/** The click payload — the drill-down hand-off (the registry's shape). */
export type ChartPanelPointClick = ChartPointClick;

/**
 * The Chart.js CONFIGURATION the panel builds — the spec's seam.
 *
 * The type is declared here (not imported from the vendor) because the
 * vendor's types would need a static `from 'chart.js'` import, which
 * the import-shape rule forbids. The shape below is the panel's own
 * contract; the vendor accepts it structurally.
 */
export interface ChartPanelDataset {
  readonly label: string;
  readonly seriesId: string;
  readonly data: readonly { readonly x: number; readonly y: number }[];
  readonly stepped: boolean;
  readonly borderColor: string;
  readonly backgroundColor: string;
  readonly pointRadius: number;
  readonly pointHoverRadius: number;
  readonly borderWidth: number;
}

export interface ChartPanelConfig {
  readonly type: 'line';
  readonly data: { readonly datasets: readonly ChartPanelDataset[] };
  readonly options: {
    readonly responsive: boolean;
    readonly maintainAspectRatio: boolean;
    readonly interaction: { readonly mode: 'index'; readonly intersect: false };
    readonly onClick: (
      event: unknown,
      elements: readonly { datasetIndex: number; index: number }[],
    ) => void;
    readonly plugins: {
      readonly legend: {
        readonly display: boolean;
        readonly onClick: (
          event: unknown,
          item: { hidden?: boolean },
          legend: { chart?: unknown },
        ) => void;
      };
      readonly tooltip: { readonly enabled: boolean };
      readonly [HOUSE_ANOMALY_PLUGIN_ID]: { readonly markers: readonly HouseAnomalyMarker[] };
      readonly [HOUSE_DATA_END_PLUGIN_ID]: { readonly dataEnd: HouseDataEnd | null };
    };
    readonly scales: {
      readonly x: {
        readonly type: 'linear';
        readonly ticks: { readonly callback: (value: number | string) => string };
      };
      readonly y: {
        readonly beginAtZero: boolean;
        readonly ticks: { readonly callback: (value: number | string) => string };
      };
    };
  };
  readonly plugins: readonly { readonly id: string }[];
}

/** The minimal Chart.js instance surface the wrapper holds. */
interface PanelChart {
  destroy(): void;
  update(): void;
  data?: unknown;
  options?: unknown;
}

/** The Chart.js module type — a TYPE-ONLY reference (no runtime import). */
type ChartJsModule = typeof import('chart.js');

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

      <canvas #canvas class="chart-panel-canvas" data-test="chart-panel-canvas"></canvas>

      <div class="chart-panel-ticks visually-hidden" data-test="chart-panel-ticks">
        @for (tick of ticks(); track tick.ms) {
          <span data-test="chart-panel-tick">{{ tick.label }}</span>
        }
      </div>

      <table class="chart-panel-data visually-hidden" data-test="chart-panel-data">
        <caption class="visually-hidden">{{ label() }}</caption>
        <thead>
          <tr>
            <th scope="col">time</th>
            <th scope="col">value</th>
            <th scope="col">series</th>
          </tr>
        </thead>
        <tbody>
          @for (row of tableRows(); track row.key) {
            <tr [attr.data-series]="row.seriesId" data-test="chart-panel-row">
              <td data-test="chart-panel-time">{{ row.time }}</td>
              <td data-test="chart-panel-value">{{ row.value }}</td>
              <td>{{ row.seriesId }}</td>
            </tr>
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
export class ChartPanelComponent implements AfterViewInit, OnDestroy {
  /** The series to render — the panel is one metric-kind + unit group. */
  readonly series = input<readonly ChartPanelSeries[]>([]);

  /**
   * The BASELINE dataset — the previous window's series (T1 phase 3, 3.7).
   *
   * This is the NORMAL path for the baseline (the Chart.js panel); the
   * house SVG carries the same capability as its fallback. The baseline
   * never adds a request of its own (it rides the panel's single series
   * fetch).
   */
  readonly baseline = input<readonly ChartPanelSeries[]>([]);

  /** The panel's accessible label. */
  readonly label = input('metrics panel');

  /** The unit suffix of the value axis — the vocabulary's placeholder when unknown. */
  readonly unit = input(METRICS_UNIT_UNKNOWN);

  /** The anomaly markers the house overlay plugin draws. */
  readonly anomalies = input<readonly HouseAnomalyMarker[]>([]);

  /**
   * WHERE THE PANEL'S DATA ENDS (T1 phase 8, task 8.10).
   *
   * The audit found a stale panel whose line simply STOPPED, with
   * nothing saying where it stopped — a reader cannot tell "the value
   * fell to zero" from "the data ends here". The marker names the
   * difference, ON the chart.
   */
  readonly dataEnd = input<HouseDataEnd | null>(null);

  /**
   * The click hand-off — the drill-down.
   *
   * A CALLBACK INPUT, for the same reason as the mount failure: the
   * consumer mounts this component through `NgComponentOutlet`.
   */
  readonly onPointClick = input<((payload: ChartPanelPointClick) => void) | null>(null);

  /**
   * The MOUNT FAILURE — the runtime fallback's trigger.
   *
   * The consumer never downgrades silently: it receives this, renders
   * the house SVG fallback and SURFACES a badge.
   *
   * A CALLBACK INPUT, not an output: the consumer mounts this component
   * through `NgComponentOutlet`, whose hand-off is input-only in this
   * Angular version.
   */
  readonly onMountFailed = input<((reason: string) => void) | null>(null);

  private readonly canvasRef = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');

  /**
   * The mounted chart — null until the vendor has loaded.
   *
   * The shell destroys the tab bodies via `NgComponentOutlet`; a missing
   * destroy leaks a canvas + a ResizeObserver PER TAB SWITCH. The
   * lifecycle hook below is therefore part of the CONTRACT, not a
   * tidy-up.
   */
  private chart: PanelChart | null = null;

  /** The mounted state (the DOM proxy + the table read from it). */
  private readonly mounted = signal(false);

  /** The time domain across every series (the shared x window). */
  readonly timeDomain = computed(() => {
    let t0 = Number.POSITIVE_INFINITY;
    let t1 = Number.NEGATIVE_INFINITY;
    for (const entry of this.series()) {
      for (const point of entry.points) {
        if (!Number.isFinite(point.t)) {
          continue;
        }
        if (point.t < t0) {
          t0 = point.t;
        }
        if (point.t > t1) {
          t1 = point.t;
        }
      }
    }
    if (!Number.isFinite(t0) || !Number.isFinite(t1)) {
      return { t0: 0, t1: 0 };
    }
    return { t0, t1 };
  });

  /** The UTC tick labels — the hidden surface's replacement for the SVG ticks. */
  readonly ticks = computed(() =>
    chartUtcTicks(this.timeDomain().t0, this.timeDomain().t1),
  );

  /** The hidden table's rows — one per sample, per series. */
  readonly tableRows = computed(() => {
    const out: { key: string; seriesId: string; time: string; value: string }[] = [];
    for (const entry of this.series()) {
      for (const point of entry.points) {
        out.push({
          key: `${entry.id}-${point.t}`,
          seriesId: entry.id,
          time: formatChartUtcTick(point.t, this.timeDomain().t1 - this.timeDomain().t0),
          value: formatChartValue(point.v),
        });
      }
    }
    return out;
  });

  constructor() {
    // The panel re-renders when its DATA changes — never on a timer and
    // never from a Chart.js redraw (the ZONELESS rule).
    effect(() => {
      const series = this.series();
      const unit = this.unit();
      const anomalies = this.anomalies();
      const mounted = this.mounted();
      if (!mounted) {
        return;
      }
      const config = this.buildChartConfig(series, unit, anomalies);
      const chart = this.chart as unknown as {
        data?: unknown;
        options?: unknown;
        update?: () => void;
      } | null;
      if (!chart) {
        return;
      }
      chart.data = config.data;
      // R1 (2026-11-04): the OPTIONS must be refreshed too, not only the
      // data.
      //
      // The house overlay's marker list lives in the chart's OPTIONS
      // (`options.plugins[HOUSE_ANOMALY_PLUGIN_ID].markers`) and the
      // chart's options are a MOUNT-TIME snapshot: the vendor resolves
      // the plugin's options from the config it was constructed with and
      // only rebuilds them when the chart is updated. Assigning only
      // `chart.data` therefore FROZE the marker list at mount time — an
      // anomalies payload that resolved AFTER the chart mounted was
      // never drawn (the panel rendered bare, with no marker and no
      // drill-down pointer). The same freeze applied to the data-end
      // marker, which travels in the same options bag.
      //
      // The assignment is the vendor's own update shape (`chart.data` +
      // `chart.options` + `chart.update()`): the options object is
      // rebuilt from the current inputs on every rebuild, so the plugin
      // reads the CURRENT marker list at draw time.
      chart.options = config.options;
      if (typeof chart.update === 'function') {
        chart.update();
      }
    });
  }

  ngAfterViewInit(): void {
    void this.mount();
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
    this.chart = null;
    this.mounted.set(false);
  }

  /**
   * The Chart.js CONFIGURATION — the spec's seam.
   *
   * The configuration is built by a pure method so every house rule
   * (the shared y-axis, the counter `stepped` flag, the UTC tick
   * callback, the legend's click-toggle, the click hand-off) is
   * assertable without reading pixels.
   */
  buildChartConfig(
    series: readonly ChartPanelSeries[],
    unit: string,
    anomalies: readonly HouseAnomalyMarker[],
  ): ChartPanelConfig {
    const { t0, t1 } = this.timeDomain();
    const spanMs = t1 - t0;
    const datasets: ChartPanelDataset[] = series.map((entry, index) => ({
      label: entry.label,
      seriesId: entry.id,
      data: entry.points.map((point) => ({ x: point.t, y: point.v })),
      // The house counter-honesty rule: a COUNTER renders STEP. A linear
      // interpolation between counter samples would assert values that
      // were never observed.
      stepped: entry.mode === 'step' ? true : false,
      borderColor: chartSeriesColor(index),
      backgroundColor: chartSeriesColor(index),
      pointRadius: 0,
      pointHoverRadius: 3,
      borderWidth: 1.6,
    }));

    return {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        // The crosshair + nearest-point snap: the pointer picks the
        // nearest sample across EVERY dataset (not the point under the
        // cursor).
        interaction: { mode: 'index', intersect: false },
        onClick: (event: unknown, elements: readonly { datasetIndex: number; index: number }[]) => {
          this.onChartClick(elements);
        },
        plugins: {
          legend: {
            display: true,
            onClick: (event: unknown, item: { hidden?: boolean }, legend: { chart?: unknown }) => {
              item.hidden = !item.hidden;
              (legend.chart as PanelChart | undefined)?.update?.();
            },
          },
          tooltip: { enabled: true },
          [HOUSE_ANOMALY_PLUGIN_ID]: { markers: anomalies },
          // The data-end marker (8.10): where the panel's data stops.
          [HOUSE_DATA_END_PLUGIN_ID]: { dataEnd: this.dataEnd() },
        },
        scales: {
          // The x-axis is LINEAR with a custom UTC tick callback — so NO
          // date-adapter dependency is needed, and the label is always
          // UTC (never local).
          x: {
            type: 'linear',
            ticks: {
              callback: (value: number | string) =>
                formatChartUtcTick(Number(value), spanMs),
            },
          },
          // ONE y-axis for every dataset — the shared-y rule.
          y: {
            beginAtZero: false,
            ticks: {
              callback: (value: number | string) =>
                `${formatChartValue(Number(value))} ${unit}`.trim(),
            },
          },
        },
      },
      plugins: [
        createHouseAnomalyOverlayPlugin(),
        createHouseDataEndOverlayPlugin(),
        createHouseCrosshairPlugin(),
      ],
    };
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
    // The vendor's tree-shakeable entry ships UNREGISTERED — the pieces
    // the panel uses are registered here, explicitly, so the lazy chunk
    // carries the panel's needs and not the whole library.
    mod.Chart.register(
      mod.LineController,
      mod.LineElement,
      mod.PointElement,
      mod.LinearScale,
      mod.CategoryScale,
      mod.Filler,
      mod.Tooltip,
      mod.Legend,
    );
    return mod.Chart;
  }

  /** The mount — the dynamic import plus the chart's construction. */
  private async mount(): Promise<void> {
    try {
      const ChartCtor = (await this.loadChartConstructor()) as ChartJsModule['Chart'];
      const canvas = this.canvasRef().nativeElement;
      const config = this.buildChartConfig(this.series(), this.unit(), this.anomalies());
      this.chart = new ChartCtor(
        canvas,
        config as unknown as ConstructorParameters<ChartJsModule['Chart']>[1],
      ) as unknown as PanelChart;
      this.mounted.set(true);
    } catch (error) {
      // A mount failure is SURFACED, never swallowed: the consumer
      // renders the house SVG fallback and shows a badge.
      const reason = error instanceof Error ? error.message : String(error);
      this.onMountFailed()?.(reason);
    }
  }

  /**
   * The click hand-off — `(datasetIndex, index)` → `(seriesId, tsMs)`.
   *
   * When the click landed on an anomaly marker, the marker id rides
   * along; otherwise the payload carries no id (never a fabricated one).
   *
   * B-1 (2026-11-04): the interaction is `mode: 'index'`, so the
   * elements list carries ONE ENTRY PER DATASET at the clicked index —
   * and an anomaly marker may be anchored to ANY dataset of the panel
   * (the vocabulary shim anchors engine-keyed markers to their panel's
   * dataset, which need not be the first). Resolution therefore scans
   * the marker-bearing element first and falls back to the FIRST
   * element's plain payload — a click on a plain sample behaves exactly
   * as before, while every anchored marker is pointer-reachable.
   */
  private onChartClick(elements: readonly { datasetIndex: number; index: number }[]): void {
    if (!elements?.length) {
      return;
    }
    let plainPayload: ChartPointClick | null = null;
    for (const element of elements) {
      const series = this.series()[element.datasetIndex];
      if (!series) {
        continue;
      }
      const point = series.points[element.index];
      if (!point) {
        continue;
      }
      const marker = houseNearestAnomaly(
        this.anomalies(),
        series.id,
        point.t,
        this.clickToleranceMs(series, element.index),
      );
      const payload: ChartPointClick = {
        anomalyId: marker?.anomalyId,
        seriesId: series.id,
        tsMs: point.t,
      };
      if (marker) {
        this.onPointClick()?.(payload);
        return;
      }
      plainPayload ??= payload;
    }
    if (plainPayload) {
      this.onPointClick()?.(plainPayload);
    }
  }

  /**
   * The click tolerance — the clicked sample's LOCAL time neighbourhood.
   *
   * The tolerance used to be the series' MEAN sample spacing — one global
   * number for the whole series. That rule cannot see a grid with a HOLE:
   * the anomaly engine buckets its series on a 5-minute stride
   * (`STATE_BUCKET_S`), while the timeline's grid is the raw 30-second
   * tier, and a daemon RESTART leaves the grid with a gap (the last
   * pre-restart sample, then the first post-restart one — ~18 minutes
   * apart in the reproduced shape).
   *
   * An anomaly whose detection bucket lands INSIDE that gap is DRAWN (its
   * time is inside the plotted window) but sits minutes away from every
   * sample — further than the mean spacing — so the click could never
   * match it and the drill-down stayed dead (the 2026-11-04 R2 residual:
   * the marker rendered, 25 clicks opened nothing).
   *
   * The tolerance is now the clicked sample's OWN neighbourhood: the
   * distance to its farther neighbour. The grid's own shape decides what
   * "near" means, and the sample that borders a gap OWNS the gap. On a
   * uniform grid this is exactly the old mean (one spacing), so the
   * normal path is unchanged.
   */
  private clickToleranceMs(series: ChartPanelSeries, index: number): number {
    const points = series.points;
    if (points.length < 2) {
      return Number.POSITIVE_INFINITY;
    }
    const here = points[index]?.t;
    if (here === undefined) {
      return Number.POSITIVE_INFINITY;
    }
    let tolerance = 0;
    if (index > 0) {
      tolerance = Math.max(tolerance, here - points[index - 1].t);
    }
    if (index + 1 < points.length) {
      tolerance = Math.max(tolerance, points[index + 1].t - here);
    }
    return tolerance;
  }
}
