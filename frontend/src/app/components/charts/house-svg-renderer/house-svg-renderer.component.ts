import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';

import { CHART_SEVERITY_COLORS } from '../chart-theme';
import {
  CHART_TICK_COUNT,
  ChartSeries,
  ChartSeriesRender,
  HouseAnomalyMarker,
  HouseGeometry,
  buildChartAreaPath,
  buildChartPath,
  chartCoords,
  chartPointTooltip,
  chartSeriesDomain,
  chartSharedDomain,
  chartUtcTicks,
  chartXScale,
  chartYScale,
  formatChartValue,
  houseCrispStrokeWidth,
  houseGeometryFor,
  houseNearestPointIndex,
  houseViewBox,
} from './house-svg-renderer';

/**
 * The CONSOLIDATED HOUSE SVG RENDERER (T1 phase 4, tasks 4.1 / 4.2 / 4.3).
 *
 * ONE engine, TWO roles (IMPL-04):
 *
 *   * the **mini** role (`variant="mini"`) — a sparkline-scale shape with
 *     NO axis, NO grid and NO legend. Per R-2 the mini role has NO page
 *     surface in T1: it is capability-retained and exercised by the
 *     renderer specs and by the mount-failure fallback;
 *   * the **fallback** role (`variant="trend"`) — the never-silent
 *     fallback a Chart.js mount failure drops to, with the badge the
 *     consumer surfaces.
 *
 * The capabilities the older engine carried and the newer one had lost
 * are all here (task 4.2's checklist): the anomaly markers with the
 * severity colours VERBATIM from the old engine, the click → drill-down
 * emit, the UTC clock formatter, and the per-series normalisation. The
 * series-toggle and window-ladder capabilities live in the PARENT (the
 * tab owns the state; the renderer draws what it is given).
 *
 * ── THE MINI EXTENSIONS (ADJ-ii — the CLOSED set) ────────────────────────
 *
 *   1. `devicePixelRatio` crispness — the stroke width divides by the
 *      ratio, so a stroke always covers whole device pixels;
 *   2. nearest-point hover via an overlay + a bisect;
 *   3. the last-value marker;
 *   4. the optional baseline line;
 *   5. the responsive `viewBox` (the geometry follows the container).
 *
 * **Nothing outside that set**: no axes grid on the mini, no zoom, no
 * brush, no legend-toggle, no export. The spec pins the absence.
 */
export type HouseSvgVariant = 'mini' | 'trend';

/** The hover state — the nearest sample under the pointer. */
export interface HouseHover {
  readonly seriesId: string;
  readonly x: number;
  readonly y: number;
  readonly title: string;
}

/** The device pixel ratio, read defensively (an SSR-ish context has none). */
function readDevicePixelRatio(): number {
  const ratio = (globalThis as { devicePixelRatio?: number }).devicePixelRatio;
  return typeof ratio === 'number' && Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
}

@Component({
  selector: 'app-house-svg-renderer',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (variant() === 'trend') {
      <div class="house-legend" data-test="house-legend">
        @for (entry of rendered(); track entry.id) {
          <span class="legend-item" [attr.data-series]="entry.id">
            <span class="legend-swatch" [style.background]="entry.color"></span>
            <span class="legend-label">{{ entry.label }}</span>
            @if (entry.sampleCount > 0) {
              <span class="legend-range"
                >{{ entry.minLabel }}–{{ entry.maxLabel }} ({{ entry.sampleCount }})</span
              >
            }
          </span>
        }
      </div>
    }

    <svg
      class="house-svg"
      [attr.viewBox]="viewBox()"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      [attr.aria-label]="ariaLabel()"
      data-test="house-svg"
    >
      <g class="house-plot">
        @if (variant() === 'trend') {
          @for (tick of ticks(); track tick.ms) {
            <line
              class="house-grid"
              [attr.x1]="tick.x"
              [attr.x2]="tick.x"
              [attr.y1]="geometry().plotTop"
              [attr.y2]="geometry().plotBottom"
              data-test="house-grid"
            ></line>
            <text
              class="house-tick-label"
              [attr.x]="tick.x"
              [attr.y]="axisLabelY()"
              text-anchor="middle"
              data-test="house-tick"
            >
              {{ tick.label }}
            </text>
          }

          <!-- The unit-labeled VALUE axis (phase 6, task 6.2): the
               panel's SHARED domain read at floor/middle/ceiling, each
               suffixed with the panel's unit. The mini renders no axis
               (R-2) — this block is trend-only. -->
          @for (label of yAxisLabels(); track label.key) {
            <text
              class="house-tick-label"
              [attr.x]="geometry().plotLeft - 6"
              [attr.y]="label.y"
              text-anchor="end"
              data-test="house-y-tick"
            >
              {{ label.value }}
            </text>
          }
        }

        @for (baseline of baselinePaths(); track baseline.id) {
          <path
            class="house-baseline"
            [attr.d]="baseline.path"
            [attr.stroke]="baseline.color"
            [attr.data-series]="baseline.id"
            data-test="house-baseline"
          ></path>
        }

        @for (entry of rendered(); track entry.id) {
          <path
            class="house-series-area"
            [attr.d]="entry.areaPath"
            [attr.fill]="entry.color"
            [attr.data-series]="entry.id"
            [attr.data-render-mode]="entry.mode"
            data-test="house-series-area"
          ></path>
          <path
            class="house-series-line"
            [attr.d]="entry.path"
            [attr.stroke]="entry.color"
            [attr.stroke-width]="strokeWidth()"
            [attr.data-series]="entry.id"
            [attr.data-render-mode]="entry.mode"
            data-test="house-series-line"
          >
            <title>{{ entry.label }}</title>
          </path>
          @for (marker of entry.markers; track marker.key) {
            <circle
              class="house-point"
              [attr.cx]="marker.x"
              [attr.cy]="marker.y"
              [attr.r]="pointRadius"
              [attr.fill]="entry.color"
              [attr.data-series]="entry.id"
              data-test="house-point"
            >
              <title>{{ marker.title }}</title>
            </circle>
          }
        }

        @for (last of lastValues(); track last.id) {
          <circle
            class="house-last-value"
            [attr.cx]="last.x"
            [attr.cy]="last.y"
            [attr.r]="pointRadius * 2"
            [attr.fill]="last.color"
            [attr.data-series]="last.id"
            data-test="house-last-value"
          >
            <title>{{ last.title }}</title>
          </circle>
        }

        <!-- ── The nearest-point hover overlay (the overlay + bisect
             extension). ────────────────────────────────────────────────
             PAINT ORDER IS LOAD-BEARING: this rect is the full-plot hit
             target (pointer-events: all), and SVG hit-testing delivers
             the pointer to the TOPMOST hit-target. It therefore sits
             ABOVE the series/areas (or a pointer over a line would never
             reach it) and BENEATH the anomaly-marker group (or the
             markers' click would never reach them — their g element is a
             SIBLING, so the click cannot bubble back down). The
             sibling ordering is pinned by the component spec ("paints
             the hover overlay BENEATH the anomaly markers") and
             exercised by a real pointer in the resources Playwright
             gate. NOTE: no backticks in this template comment — the
             template is a TS template literal. -->
        <rect
          class="house-overlay"
          [attr.x]="geometry().plotLeft"
          [attr.y]="geometry().plotTop"
          [attr.width]="plotWidth()"
          [attr.height]="plotHeight()"
          fill="transparent"
          data-test="house-hover-overlay"
          (pointermove)="onPointerMove($event)"
          (pointerleave)="onPointerLeave()"
        ></rect>

        @for (marker of anomalyMarkers(); track marker.anomaly.anomalyId ?? $index) {
          <g
            class="house-anomaly-marker"
            [attr.data-anomaly-id]="marker.anomaly.anomalyId"
            data-test="house-anomaly-marker"
            (click)="handleAnomalyClick(marker.anomaly)"
          >
            <line
              class="house-marker-stem"
              [attr.x1]="marker.cx"
              [attr.x2]="marker.cx"
              [attr.y1]="geometry().plotTop"
              [attr.y2]="geometry().plotBottom"
            ></line>
            <circle
              class="house-marker-dot"
              [attr.cx]="marker.cx"
              [attr.cy]="markerLaneY"
              [attr.r]="markerRadius"
              [attr.fill]="marker.color"
              data-test="house-anomaly-marker-dot"
            >
              <title>{{ marker.anomaly.title }}</title>
            </circle>
            <circle
              class="house-marker-hit"
              [attr.cx]="marker.cx"
              [attr.cy]="markerLaneY"
              [attr.r]="markerHitRadius"
              fill="transparent"
            ></circle>
          </g>
        }

        @if (hovered(); as hover) {
          <circle
            class="house-hover-point"
            [attr.cx]="hover.x"
            [attr.cy]="hover.y"
            [attr.r]="pointRadius * 2.5"
            [attr.fill]="'#f1f5f9'"
            [attr.data-series]="hover.seriesId"
            data-test="house-hover-point"
          >
            <title>{{ hover.title }}</title>
          </circle>
        }

        @if (!hasValues()) {
          <text
            class="house-empty"
            [attr.x]="plotMidX()"
            [attr.y]="plotMidY()"
            text-anchor="middle"
            data-test="house-empty"
          >
            No samples in window
          </text>
        }
      </g>

      @if (variant() === 'trend') {
        <text
          class="house-axis-caption"
          [attr.x]="geometry().plotLeft"
          [attr.y]="captionY()"
          data-test="house-axis-caption"
          >UTC</text
        >
      }
    </svg>
  `,
  styles: [
    `
      :host {
        display: block;
        width: 100%;
      }

      .house-legend {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem 1rem;
        margin-bottom: 0.35rem;
        font-size: 0.72rem;
        color: #94a3b8;
      }

      .legend-item {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
      }

      .legend-swatch {
        width: 0.6rem;
        height: 0.6rem;
        border-radius: 2px;
        flex: 0 0 auto;
      }

      .legend-range {
        color: #64748b;
        font-variant-numeric: tabular-nums;
      }

      .house-svg {
        display: block;
        width: 100%;
        height: auto;
        overflow: visible;
      }

      .house-grid {
        stroke: #1e293b;
        stroke-width: 1;
      }

      .house-tick-label,
      .house-axis-caption,
      .house-empty {
        fill: #64748b;
        font-size: 11px;
      }

      .house-series-line {
        fill: none;
        vector-effect: non-scaling-stroke;
      }

      .house-series-area {
        opacity: 0.16;
        stroke: none;
      }

      .house-point {
        opacity: 0.75;
      }

      .house-last-value {
        opacity: 0.95;
      }

      .house-baseline {
        fill: none;
        stroke-dasharray: 4 3;
        stroke-width: 1;
        opacity: 0.55;
      }

      .house-hover-point {
        opacity: 0.9;
      }

      .house-overlay {
        pointer-events: all;
      }

      .house-anomaly-marker {
        cursor: pointer;
      }

      .house-marker-stem {
        stroke: #334155;
        stroke-width: 1;
        stroke-dasharray: 3 4;
      }

      .house-marker-dot {
        stroke: #0f172a;
        stroke-width: 1.5;
      }

      .house-marker-hit {
        pointer-events: all;
      }

      .house-anomaly-marker:hover .house-marker-dot {
        stroke: #f1f5f9;
      }
    `,
  ],
})
export class HouseSvgRendererComponent implements AfterViewInit, OnDestroy {
  /** The series to draw. */
  readonly series = input<readonly ChartSeries[]>([]);

  /** The anomaly markers to draw (the severity colours are the house palette). */
  readonly anomalies = input<readonly HouseAnomalyMarker[]>([]);

  /** The OPTIONAL baseline series (the mini extension's baseline line). */
  readonly baseline = input<readonly ChartSeries[]>([]);

  /** The role this instance plays — the mini shape or the fallback shape. */
  readonly variant = input<HouseSvgVariant>('mini');

  /**
   * The panel's unit — the suffix of the value-axis labels (the trend
   * role; phase 6, task 6.2). The mini renders no axis at all (R-2),
   * so it ignores this. `unit unknown` renders verbatim — never a
   * guess.
   */
  readonly unit = input<string>('unit unknown');

  /** The accessible name of the chart. */
  readonly ariaLabel = input<string>('Metric trend (UTC)');

  /**
   * The device pixel ratio override.
   *
   * `null` means "read the ambient ratio"; a number pins it (the spec
   * pins both paths).
   */
  readonly devicePixelRatio = input<number | null>(null);

  /**
   * The anomaly marker click — the drill-down hand-off.
   *
   * A CALLBACK INPUT, not an output: the consumer mounts this component
   * through `NgComponentOutlet`, whose hand-off is input-only in this
   * Angular version. The renderer calls the handler it was given.
   */
  readonly onAnomalyClick = input<((marker: HouseAnomalyMarker) => void) | null>(null);

  private readonly elementRef = inject(ElementRef<HTMLElement>);
  private readonly measured = signal<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });
  private resizeObserver: ResizeObserver | null = null;

  /** The nearest sample under the pointer (the hover extension). */
  readonly hovered = signal<HouseHover | null>(null);

  /** The drawing geometry — the house one until a container is measured. */
  readonly geometry = computed<HouseGeometry>(() =>
    houseGeometryFor(this.measured().width, this.measured().height),
  );

  /** The responsive viewBox (the mini extension's viewBox responsiveness). */
  readonly viewBox = computed(() => houseViewBox(this.geometry()));

  /** The sanitised device pixel ratio. */
  readonly crispRatio = computed(() => {
    const declared = this.devicePixelRatio();
    const ratio = declared ?? readDevicePixelRatio();
    return Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
  });

  /** The stroke width for the current ratio (the crispness extension). */
  readonly strokeWidth = computed(() => houseCrispStrokeWidth(this.crispRatio()));

  readonly pointRadius = 2.2;
  readonly markerLaneY = 24;
  readonly markerRadius = 6;
  readonly markerHitRadius = 12;

  readonly plotWidth = computed(
    () => this.geometry().plotRight - this.geometry().plotLeft,
  );
  readonly plotHeight = computed(
    () => this.geometry().plotBottom - this.geometry().plotTop,
  );
  readonly plotMidX = computed(
    () => this.geometry().plotLeft + this.plotWidth() / 2,
  );
  readonly plotMidY = computed(
    () => this.geometry().plotTop + this.plotHeight() / 2,
  );
  readonly axisLabelY = computed(() => this.geometry().plotBottom + 18);
  readonly captionY = computed(() => this.geometry().viewHeight - 4);

  /** The time domain across every series. */
  private readonly timeDomain = computed(() => {
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

  /**
   * The PANEL'S SHARED DOMAIN (phase 6, task 6.1 / W7).
   *
   * ONE [min,max] across every series handed to this engine (plus the
   * baseline) — the panel is a metric-kind + unit group, so its series
   * are commensurate by construction. EVERY series is plotted against
   * THIS domain; no series is ever independently rescaled inside the
   * panel. The pure helper is exported so the W7 numeric assertion can
   * be planted against (a per-series rescale here fails the spec).
   */
  readonly panelDomain = computed(() =>
    chartSharedDomain([...this.series(), ...this.baseline()]),
  );

  /**
   * The per-series projection (paths, ranges, tooltip markers) — every
   * series against the PANEL'S single shared domain (W7). The legend's
   * min–max labels stay the series' OWN observed values (a per-series
   * FACT — the legend annotates values, the AXIS is the panel's).
   */
  readonly rendered = computed<readonly ChartSeriesRender[]>(() => {
    const { t0, t1 } = this.timeDomain();
    const geometry = this.geometry();
    const domain = this.panelDomain();
    return this.series().map((entry) => {
      const own = chartSeriesDomain(entry.points);
      const coords = chartCoords(entry.points, t0, t1, domain.min, domain.max, geometry);
      const markers: ChartSeriesRender['markers'][number][] = [];
      let index = 0;
      for (const point of entry.points) {
        if (!Number.isFinite(point.t) || !Number.isFinite(point.v)) {
          continue;
        }
        markers.push({
          key: `${entry.id}-${index}`,
          x: chartXScale(point.t, t0, t1, geometry),
          y: chartYScale(point.v, domain.min, domain.max, geometry),
          title: chartPointTooltip(point),
        });
        index += 1;
      }
      return {
        id: entry.id,
        label: entry.label,
        color: entry.color,
        mode: entry.mode,
        path: buildChartPath(coords, entry.mode),
        areaPath: buildChartAreaPath(coords, entry.mode, geometry),
        min: own.min,
        max: own.max,
        minLabel: formatChartValue(own.min),
        maxLabel: formatChartValue(own.max),
        sampleCount: coords.length,
        markers,
      };
    });
  });

  /** The baseline series paths (the optional baseline line extension). */
  readonly baselinePaths = computed(() => {
    const { t0, t1 } = this.timeDomain();
    const geometry = this.geometry();
    const domain = this.panelDomain();
    return this.baseline().map((entry) => {
      const coords = chartCoords(entry.points, t0, t1, domain.min, domain.max, geometry);
      return { id: entry.id, color: entry.color, path: buildChartPath(coords, entry.mode) };
    });
  });

  /**
   * The value-axis labels (the trend role ONLY — phase 6, task 6.2).
   *
   * The panel's SHARED domain read at three heights (floor / middle /
   * ceiling), each suffixed with the panel's unit. The mini renders no
   * axis at all (R-2) — the exemption is asserted in the renderer spec.
   */
  readonly yAxisLabels = computed(() => {
    if (this.variant() !== 'trend') {
      return [];
    }
    const { min, max } = this.panelDomain();
    if (!Number.isFinite(min) || !Number.isFinite(max) || this.series().length === 0) {
      return [];
    }
    const mid = min + (max - min) / 2;
    const suffix = this.unit();
    const label = (value: number) => `${formatChartValue(value)} ${suffix}`.trim();
    return [
      { key: 'max', value: label(max), y: this.geometry().plotTop + 3 },
      { key: 'mid', value: label(mid), y: this.plotMidY() + 3 },
      { key: 'min', value: label(min), y: this.geometry().plotBottom + 3 },
    ];
  });

  /** The UTC ticks (the TREND variant only — the mini renders no axis). */
  readonly ticks = computed(() =>
    this.variant() === 'trend'
      ? chartUtcTicks(
          this.timeDomain().t0,
          this.timeDomain().t1,
          CHART_TICK_COUNT,
          this.geometry(),
        )
      : [],
  );

  /** The anomaly markers, positioned on the time axis. */
  readonly anomalyMarkers = computed(() => {
    const { t0, t1 } = this.timeDomain();
    const geometry = this.geometry();
    return this.anomalies().map((anomaly) => ({
      anomaly,
      cx: chartXScale(anomaly.tsMs, t0, t1, geometry),
      color: CHART_SEVERITY_COLORS[anomaly.severity] ?? CHART_SEVERITY_COLORS.medium,
    }));
  });

  /** The last-value marker of every series (the mini extension). */
  readonly lastValues = computed(() => {
    const out: { id: string; color: string; x: number; y: number; title: string }[] = [];
    for (const entry of this.rendered()) {
      const last = entry.markers[entry.markers.length - 1];
      if (!last) {
        continue;
      }
      out.push({ id: entry.id, color: entry.color, x: last.x, y: last.y, title: last.title });
    }
    return out;
  });

  /** Whether any series carries a finite sample. */
  readonly hasValues = computed(() =>
    this.rendered().some((entry) => entry.sampleCount > 0),
  );

  ngAfterViewInit(): void {
    this.measure();
    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObserver = new ResizeObserver(() => this.measure());
      this.resizeObserver.observe(this.elementRef.nativeElement);
    }
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
  }

  /** The anomaly marker click → the drill-down hand-off. */
  handleAnomalyClick(anomaly: HouseAnomalyMarker): void {
    this.onAnomalyClick()?.(anomaly);
  }

  /**
   * The nearest-point hover (the overlay + bisect extension).
   *
   * The pointer's position is converted into the viewBox's coordinate
   * system, then into a timestamp, then into the NEAREST sample by a
   * bisect over the sorted sample times.
   */
  onPointerMove(event: PointerEvent): void {
    const svg = this.elementRef.nativeElement?.querySelector('svg');
    const rect = svg?.getBoundingClientRect?.();
    if (!rect || rect.width <= 0) {
      return;
    }
    const geometry = this.geometry();
    const plotWidth = this.plotWidth();
    if (plotWidth <= 0) {
      return;
    }
    const viewX = ((event.clientX - rect.left) / rect.width) * geometry.viewWidth;
    const { t0, t1 } = this.timeDomain();
    if (!(t1 > t0)) {
      return;
    }
    const fraction = (viewX - geometry.plotLeft) / plotWidth;
    const ms = t0 + fraction * (t1 - t0);

    let best: (HouseHover & { distance: number }) | null = null;
    const domain = this.panelDomain();
    for (const entry of this.series()) {
      const samples = entry.points.filter(
        (point) => Number.isFinite(point.t) && Number.isFinite(point.v),
      );
      const times = samples.map((point) => point.t);
      const index = houseNearestPointIndex(times, ms);
      if (index < 0) {
        continue;
      }
      const point = samples[index];
      const distance = Math.abs(point.t - ms);
      if (best && distance >= best.distance) {
        continue;
      }
      best = {
        seriesId: entry.id,
        x: chartXScale(point.t, t0, t1, geometry),
        y: chartYScale(point.v, domain.min, domain.max, geometry),
        title: chartPointTooltip(point),
        distance,
      };
    }
    this.hovered.set(best ? { ...best } : null);
  }

  /** The pointer left the overlay — the hover state clears. */
  onPointerLeave(): void {
    this.hovered.set(null);
  }

  /** The container measurement — the responsive viewBox's input. */
  private measure(): void {
    const host = this.elementRef.nativeElement;
    const rect = host?.getBoundingClientRect?.();
    this.measured.set({ width: rect?.width ?? 0, height: rect?.height ?? 0 });
  }
}
