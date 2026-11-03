import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';
import { MetricsChartRole } from '../../../models/metrics.model';
import { CHART_EMPTY_VALUE } from '../chart-theme';

/**
 * Dependency-free inline SVG chart primitive (P5 task 5.3 · D2 — the
 * zero-dependency guard; the house precedent is
 * `pages/resources/svg-timeline/svg-timeline.component.ts`: "no chart
 * library exists and v1 adds none").
 *
 * ONE shared primitive — never a per-page copy. It renders:
 *   - LINES and STEPS (a counter renders STEP: a linear interpolation
 *     between counter samples would assert values that were never
 *     observed — data honesty; the mode comes from the family
 *     metadata `chart_role`, contract §4.3);
 *   - AREAS (the fill under a line/step);
 *   - a LEGEND with each series' observed range;
 *   - native `<title>` tooltips (one per sample);
 *   - UTC axis labels (never local time).
 *
 * FEATURE-FROZEN: zoom / brush / crosshair / gradients are OUT (the
 * same closure `svg-timeline` declared).
 *
 * The scale + path helpers are EXPORTED PURE FUNCTIONS so they can be
 * asserted directly in a unit spec (the house `svg-timeline:88-114`
 * pattern) — the component owns no HTTP, no polling and no state.
 *
 * Scaling note: the series carry different units, so each series is
 * normalised to its OWN observed [min,max] and the legend prints that
 * range. This is a SHAPE view — the numeric truth lives in the
 * Overview tab's stat cards.
 */

// ── Geometry (fixed viewBox; the text is never stretched) ─────────────

export const CHART_VIEW_WIDTH = 900;
export const CHART_VIEW_HEIGHT = 260;
export const CHART_PLOT_LEFT = 56;
export const CHART_PLOT_RIGHT = CHART_VIEW_WIDTH - 16;
export const CHART_PLOT_TOP = 18;
export const CHART_PLOT_BOTTOM = CHART_VIEW_HEIGHT - 34;
export const CHART_PLOT_WIDTH = CHART_PLOT_RIGHT - CHART_PLOT_LEFT;
export const CHART_PLOT_HEIGHT = CHART_PLOT_BOTTOM - CHART_PLOT_TOP;
export const CHART_TICK_COUNT = 5;
export const CHART_POINT_RADIUS = 2.2;

/** One sample — `t` is epoch MILLISECONDS, `v` the observed value. */
export interface ChartPoint {
  readonly t: number;
  readonly v: number;
}

/** One series — the primitive's input shape. */
export interface ChartSeries {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  /** The rendering mode — `step` for counters, `line` for gauges. */
  readonly mode: MetricsChartRole;
  readonly points: readonly ChartPoint[];
}

export interface ChartCoord {
  readonly x: number;
  readonly y: number;
}

export interface ChartTick {
  readonly x: number;
  readonly label: string;
  readonly ms: number;
}

export interface ChartMarker {
  readonly key: string;
  readonly x: number;
  readonly y: number;
  readonly title: string;
}

/** A rendered series — the component's per-series projection. */
export interface ChartSeriesRender {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  readonly mode: MetricsChartRole;
  readonly path: string;
  readonly areaPath: string;
  readonly min: number;
  readonly max: number;
  readonly minLabel: string;
  readonly maxLabel: string;
  readonly sampleCount: number;
  readonly markers: readonly ChartMarker[];
}

// ── Pure scale / path helpers (exported for direct unit assertions) ──

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** Time (epoch ms) → viewBox x. `t1 <= t0` degenerates to the midpoint. */
export function chartXScale(ms: number, t0: number, t1: number): number {
  if (!Number.isFinite(ms)) {
    return CHART_PLOT_LEFT;
  }
  if (!(t1 > t0)) {
    return CHART_PLOT_LEFT + CHART_PLOT_WIDTH / 2;
  }
  return CHART_PLOT_LEFT + clamp01((ms - t0) / (t1 - t0)) * CHART_PLOT_WIDTH;
}

/**
 * Value → viewBox y (inverted), NORMALISED against the series' own
 * observed [min,max]. A value below `min` clamps to the floor and a
 * value above `max` clamps to the ceiling — the axis never shows a
 * value that was not observed.
 */
export function chartYScale(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return CHART_PLOT_BOTTOM;
  }
  if (!(max > min)) {
    return CHART_PLOT_TOP + CHART_PLOT_HEIGHT / 2;
  }
  return CHART_PLOT_BOTTOM - clamp01((value - min) / (max - min)) * CHART_PLOT_HEIGHT;
}

/** The observed [min,max] of a series (the per-series normalisation). */
export function chartSeriesDomain(points: readonly ChartPoint[]): {
  min: number;
  max: number;
} {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const point of points) {
    if (!Number.isFinite(point.v)) {
      continue;
    }
    if (point.v < min) {
      min = point.v;
    }
    if (point.v > max) {
      max = point.v;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return { min: 0, max: 0 };
  }
  return { min, max };
}

/** The plot coordinates of a series (the non-finite samples are dropped). */
export function chartCoords(
  points: readonly ChartPoint[],
  t0: number,
  t1: number,
  min: number,
  max: number,
): ChartCoord[] {
  const out: ChartCoord[] = [];
  for (const point of points) {
    if (!Number.isFinite(point.t) || !Number.isFinite(point.v)) {
      continue;
    }
    out.push({ x: chartXScale(point.t, t0, t1), y: chartYScale(point.v, min, max) });
  }
  return out;
}

/** `M x0,y0 L x1,y1 …` — the empty string for an empty series. */
export function buildChartLinePath(coords: readonly ChartCoord[]): string {
  if (coords.length === 0) {
    return '';
  }
  return coords
    .map((coord, index) => `${index === 0 ? 'M' : 'L'}${coord.x.toFixed(2)},${coord.y.toFixed(2)}`)
    .join(' ');
}

/**
 * `M x0,y0 H x1 V y1 H x2 V y2 …` — the STEP path.
 *
 * The horizontal-then-vertical form is the step-after semantics: the
 * observed value HOLDS until the next sample. The `H`/`V` commands are
 * what make the step shape explicit (and assertable) — a linear path
 * has neither.
 */
export function buildChartStepPath(coords: readonly ChartCoord[]): string {
  if (coords.length === 0) {
    return '';
  }
  const first = coords[0];
  let path = `M${first.x.toFixed(2)},${first.y.toFixed(2)}`;
  for (let index = 1; index < coords.length; index += 1) {
    const coord = coords[index];
    path += ` H${coord.x.toFixed(2)} V${coord.y.toFixed(2)}`;
  }
  return path;
}

/** The path for a series in its own rendering mode. */
export function buildChartPath(
  coords: readonly ChartCoord[],
  mode: MetricsChartRole,
): string {
  return mode === 'step' ? buildChartStepPath(coords) : buildChartLinePath(coords);
}

/** The series path closed to the plot floor — the area fill. */
export function buildChartAreaPath(
  coords: readonly ChartCoord[],
  mode: MetricsChartRole,
): string {
  if (coords.length === 0) {
    return '';
  }
  const body = buildChartPath(coords, mode);
  const first = coords[0];
  const last = coords[coords.length - 1];
  return `${body} L${last.x.toFixed(2)},${CHART_PLOT_BOTTOM} L${first.x.toFixed(
    2,
  )},${CHART_PLOT_BOTTOM} Z`;
}

/** The UTC tick positions + labels across the window. */
export function chartUtcTicks(
  t0: number,
  t1: number,
  count: number = CHART_TICK_COUNT,
): ChartTick[] {
  if (!(t1 > t0) || count <= 0) {
    return [];
  }
  const out: ChartTick[] = [];
  for (let index = 0; index < count; index += 1) {
    const fraction = count === 1 ? 0 : index / (count - 1);
    const ms = t0 + (t1 - t0) * fraction;
    out.push({ x: chartXScale(ms, t0, t1), label: formatChartUtc(ms), ms });
  }
  return out;
}

/** UTC `HH:MM` label for an epoch-ms tick (never local time). */
export function formatChartUtc(ms: number): string {
  if (!Number.isFinite(ms)) {
    return CHART_EMPTY_VALUE;
  }
  return new Date(ms).toISOString().slice(11, 16);
}

/** A compact value label (integers stay integers). */
export function formatChartValue(value: number): string {
  if (!Number.isFinite(value)) {
    return CHART_EMPTY_VALUE;
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toFixed(2);
}

/** The native-tooltip text of a sample. */
export function chartPointTooltip(point: ChartPoint): string {
  return `${formatChartUtc(point.t)} UTC · ${formatChartValue(point.v)}`;
}

// ── The component ──────────────────────────────────────────────────────

@Component({
  selector: 'app-svg-line-chart',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="chart-legend" data-test="chart-legend">
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

    <svg
      class="chart-svg"
      [attr.viewBox]="viewBox"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      [attr.aria-label]="ariaLabel()"
      data-test="svg-line-chart"
    >
      <g class="chart-plot">
        @for (tick of ticks(); track tick.ms) {
          <line
            class="chart-grid"
            [attr.x1]="tick.x"
            [attr.x2]="tick.x"
            [attr.y1]="plotTop"
            [attr.y2]="plotBottom"
          ></line>
          <text
            class="chart-tick-label"
            [attr.x]="tick.x"
            [attr.y]="axisLabelY"
            text-anchor="middle"
            data-test="chart-tick"
          >
            {{ tick.label }}
          </text>
        }

        @for (entry of rendered(); track entry.id) {
          <path
            class="series-area"
            [attr.d]="entry.areaPath"
            [attr.fill]="entry.color"
            [attr.data-series]="entry.id"
            [attr.data-render-mode]="entry.mode"
            data-test="series-area"
          ></path>
          <path
            class="series-line"
            [attr.d]="entry.path"
            [attr.stroke]="entry.color"
            [attr.data-series]="entry.id"
            [attr.data-render-mode]="entry.mode"
            data-test="series-line"
          >
            <title>{{ entry.label }}</title>
          </path>
          @for (marker of entry.markers; track marker.key) {
            <circle
              class="series-point"
              [attr.cx]="marker.x"
              [attr.cy]="marker.y"
              [attr.r]="pointRadius"
              [attr.fill]="entry.color"
              [attr.data-series]="entry.id"
              data-test="chart-point"
            >
              <title>{{ marker.title }}</title>
            </circle>
          }
        }

        @if (!hasValues()) {
          <text
            class="chart-empty"
            [attr.x]="plotMidX"
            [attr.y]="plotMidY"
            text-anchor="middle"
            data-test="chart-empty"
          >
            No samples in window
          </text>
        }
      </g>

      <text class="chart-axis-caption" [attr.x]="plotLeft" [attr.y]="captionY">UTC</text>
    </svg>
  `,
  styles: [
    `
      :host {
        display: block;
        width: 100%;
      }

      .chart-legend {
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

      .chart-svg {
        display: block;
        width: 100%;
        height: auto;
        overflow: visible;
      }

      .chart-grid {
        stroke: #1e293b;
        stroke-width: 1;
      }

      .chart-tick-label,
      .chart-axis-caption,
      .chart-empty {
        fill: #64748b;
        font-size: 11px;
      }

      .series-line {
        fill: none;
        stroke-width: 1.6;
      }

      .series-area {
        opacity: 0.16;
        stroke: none;
      }

      .series-point {
        opacity: 0.75;
      }
    `,
  ],
})
export class SvgLineChartComponent {
  /** The series to render. */
  readonly series = input<readonly ChartSeries[]>([]);

  /** The accessible name of the chart. */
  readonly ariaLabel = input<string>('Metric trend (UTC)');

  readonly viewBox = `0 0 ${CHART_VIEW_WIDTH} ${CHART_VIEW_HEIGHT}`;
  readonly plotLeft = CHART_PLOT_LEFT;
  readonly plotTop = CHART_PLOT_TOP;
  readonly plotBottom = CHART_PLOT_BOTTOM;
  readonly pointRadius = CHART_POINT_RADIUS;
  readonly plotMidX = CHART_PLOT_LEFT + CHART_PLOT_WIDTH / 2;
  readonly plotMidY = CHART_PLOT_TOP + CHART_PLOT_HEIGHT / 2;
  readonly axisLabelY = CHART_PLOT_BOTTOM + 18;
  readonly captionY = CHART_VIEW_HEIGHT - 4;

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

  readonly ticks = computed(() => chartUtcTicks(this.timeDomain().t0, this.timeDomain().t1));

  /** The per-series projection (paths, ranges, tooltip markers). */
  readonly rendered = computed<readonly ChartSeriesRender[]>(() => {
    const { t0, t1 } = this.timeDomain();
    return this.series().map((entry) => {
      const domain = chartSeriesDomain(entry.points);
      const coords = chartCoords(entry.points, t0, t1, domain.min, domain.max);
      const markers: ChartMarker[] = [];
      let index = 0;
      for (const point of entry.points) {
        if (!Number.isFinite(point.t) || !Number.isFinite(point.v)) {
          continue;
        }
        markers.push({
          key: `${entry.id}-${index}`,
          x: chartXScale(point.t, t0, t1),
          y: chartYScale(point.v, domain.min, domain.max),
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
        areaPath: buildChartAreaPath(coords, entry.mode),
        min: domain.min,
        max: domain.max,
        minLabel: formatChartValue(domain.min),
        maxLabel: formatChartValue(domain.max),
        sampleCount: coords.length,
        markers,
      };
    });
  });

  readonly hasValues = computed(() =>
    this.rendered().some((entry) => entry.sampleCount > 0),
  );
}
