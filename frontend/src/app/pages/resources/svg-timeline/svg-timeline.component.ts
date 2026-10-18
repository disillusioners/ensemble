import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';
import {
  Anomaly,
  AnomalySeverity,
  HistoryPoint,
  parseNaiveUtc,
} from '../../../models/resources.models';

/**
 * Dependency-free inline SVG timeline primitive (D4 — no chart library
 * exists and v1 adds none).
 *
 * PRESENTATIONAL ONLY: it owns no HTTP, no polling, no state. The
 * Timeline tab feeds it `points` + `anomalies` + the set of enabled
 * series and reacts to `markerClick`.
 *
 * v1 shape is deliberately closed (§1 / R1): line + area per series, a
 * UTC time axis, anomaly marker dots, and a click → window emit.
 * Zoom / brush / tooltips-beyond-native-title / gradients are OUT OF
 * SCOPE. `<title>` gives each marker a native tooltip.
 *
 * Scaling note: the five series carry different units (%CPU, MB,
 * threads, instance count), so there is no honest shared y-axis in v1.
 * Each series is normalised to its own observed [min,max] and drawn in
 * its own colour; the legend prints that range. This is a SHAPE view —
 * the numeric truth lives in the Live tab and the drill-down panel.
 */

export type TimelineSeriesKey =
  | 'daemon_rss_mb'
  | 'cgroup_mem_mb'
  | 'daemon_cpu'
  | 'threads'
  | 'instances_active';

export interface TimelineSeriesDef {
  readonly key: TimelineSeriesKey;
  readonly label: string;
  readonly color: string;
}

/** Default-on series per T2: memory (both) + CPU. */
export const DEFAULT_TIMELINE_SERIES: readonly TimelineSeriesKey[] = [
  'daemon_rss_mb',
  'cgroup_mem_mb',
  'daemon_cpu',
];

export const TIMELINE_SERIES_DEFS: readonly TimelineSeriesDef[] = [
  { key: 'daemon_rss_mb', label: 'Daemon RSS (MB)', color: '#38bdf8' },
  { key: 'cgroup_mem_mb', label: 'Cgroup memory (MB)', color: '#a78bfa' },
  { key: 'daemon_cpu', label: 'Daemon CPU (%)', color: '#34d399' },
  { key: 'threads', label: 'Threads', color: '#fbbf24' },
  { key: 'instances_active', label: 'Active instances', color: '#f472b6' },
];

// ── Geometry (fixed viewBox; text never stretched) ──────────────────

export const TIMELINE_VIEW_WIDTH = 1000;
export const TIMELINE_VIEW_HEIGHT = 320;
export const TIMELINE_PLOT_LEFT = 48;
export const TIMELINE_PLOT_RIGHT = TIMELINE_VIEW_WIDTH - 12;
export const TIMELINE_PLOT_TOP = 40;
export const TIMELINE_PLOT_BOTTOM = TIMELINE_VIEW_HEIGHT - 32;
export const TIMELINE_PLOT_WIDTH = TIMELINE_PLOT_RIGHT - TIMELINE_PLOT_LEFT;
export const TIMELINE_PLOT_HEIGHT = TIMELINE_PLOT_BOTTOM - TIMELINE_PLOT_TOP;
export const TIMELINE_MARKER_LANE_Y = 24;
export const TIMELINE_MARKER_RADIUS = 6;
export const TIMELINE_MARKER_HIT_RADIUS = 12;
export const TIMELINE_TICK_COUNT = 5;

/** Emitted on a marker click — the traceback drill-down window. */
export interface TimelineMarkerClick {
  anomaly: Anomaly;
  /** `t_start − pad`, ISO-8601 UTC (aware). */
  t0: string;
  /** `t_end + pad`, ISO-8601 UTC (aware). */
  t1: string;
}

// ── Pure scale / path helpers (exported for direct unit assertions) ──

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** Time → viewBox x. `t1 <= t0` degenerates to the plot midpoint. */
export function timelineXScale(ms: number, t0: number, t1: number): number {
  if (!Number.isFinite(ms)) {
    return TIMELINE_PLOT_LEFT;
  }
  if (!(t1 > t0)) {
    return TIMELINE_PLOT_LEFT + TIMELINE_PLOT_WIDTH / 2;
  }
  return TIMELINE_PLOT_LEFT + clamp01((ms - t0) / (t1 - t0)) * TIMELINE_PLOT_WIDTH;
}

/** Value → viewBox y (inverted; per-series min/max). */
export function timelineYScale(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return TIMELINE_PLOT_BOTTOM;
  }
  if (!(max > min)) {
    return TIMELINE_PLOT_TOP + TIMELINE_PLOT_HEIGHT / 2;
  }
  return TIMELINE_PLOT_BOTTOM - clamp01((value - min) / (max - min)) * TIMELINE_PLOT_HEIGHT;
}

export interface TimelineCoord {
  x: number;
  y: number;
}

/** `M x0,y0 L x1,y1 …` — empty string for an empty series. */
export function buildLinePath(coords: readonly TimelineCoord[]): string {
  if (coords.length === 0) {
    return '';
  }
  return coords
    .map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(2)},${c.y.toFixed(2)}`)
    .join(' ');
}

/** Line path closed to the plot floor — the area fill. */
export function buildAreaPath(coords: readonly TimelineCoord[]): string {
  if (coords.length === 0) {
    return '';
  }
  const first = coords[0];
  const last = coords[coords.length - 1];
  const body = coords
    .map((c) => `L${c.x.toFixed(2)},${c.y.toFixed(2)}`)
    .join(' ');
  return `M${first.x.toFixed(2)},${TIMELINE_PLOT_BOTTOM} ${body} L${last.x.toFixed(
    2,
  )},${TIMELINE_PLOT_BOTTOM} Z`;
}

/**
 * D10 pad rule for the drill-down window:
 * `pad = max(60 s, 10 % of the anomaly span)`.
 *
 * Returns aware ISO-8601 UTC bounds (the `/traceback` contract expects
 * offset-bearing timestamps).
 */
export function anomalyDrilldownWindow(anomaly: Anomaly): { t0: string; t1: string } {
  const startMs = parseNaiveUtc(anomaly.t_start).getTime();
  const endMs = parseNaiveUtc(anomaly.t_end).getTime();
  const span = Math.max(0, endMs - startMs);
  const padMs = Math.max(60_000, span * 0.1);
  return {
    t0: new Date(startMs - padMs).toISOString(),
    t1: new Date(endMs + padMs).toISOString(),
  };
}

/** UTC `HH:MM` label for an epoch-ms tick (D10 — never local time). */
export function formatUtcClock(ms: number): string {
  return new Date(ms).toISOString().slice(11, 16);
}

const SEVERITY_COLORS: Record<AnomalySeverity, string> = {
  low: '#38bdf8',
  medium: '#f59e0b',
  high: '#ef4444',
};

@Component({
  selector: 'app-svg-timeline',
  standalone: true,
  imports: [DecimalPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="timeline-legend" data-test="series-legend">
      @for (series of seriesPaths(); track series.key) {
        <span class="legend-item" [attr.data-series]="series.key">
          <span class="legend-swatch" [style.background]="series.color"></span>
          <span class="legend-label">{{ series.label }}</span>
          @if (series.hasValues) {
            <span class="legend-range">{{ series.min | number: '1.0-1' }}–{{ series.max | number: '1.0-1' }}</span>
          }
        </span>
      }
    </div>

    <svg
      class="timeline-svg"
      [attr.viewBox]="viewBox"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Resource history timeline (UTC)"
      data-test="svg-timeline"
    >
      <g class="timeline-plot">
        @for (series of seriesPaths(); track series.key) {
          <path
            class="series-area"
            [attr.d]="series.area"
            [attr.fill]="series.color"
            [attr.data-series]="series.key"
          ></path>
          <path
            class="series-line"
            [attr.d]="series.line"
            [attr.stroke]="series.color"
            [attr.data-series]="series.key"
            data-test="series-line"
          ></path>
        }

        @for (tick of ticks(); track tick.x) {
          <line
            class="timeline-grid"
            [attr.x1]="tick.x"
            [attr.x2]="tick.x"
            [attr.y1]="plotTop"
            [attr.y2]="plotBottom"
          ></line>
          <text
            class="timeline-tick-label"
            [attr.x]="tick.x"
            [attr.y]="axisLabelY"
            text-anchor="middle"
            data-test="timeline-tick"
          >{{ tick.label }}</text>
        }

        @for (marker of markers(); track marker.anomaly.id) {
          <g
            class="anomaly-marker"
            data-test="anomaly-marker"
            [attr.data-anomaly-id]="marker.anomaly.id"
            (click)="onMarkerClick(marker.anomaly)"
          >
            <line
              class="marker-stem"
              [attr.x1]="marker.cx"
              [attr.x2]="marker.cx"
              [attr.y1]="plotTop"
              [attr.y2]="plotBottom"
            ></line>
            <circle
              class="marker-dot"
              [attr.cx]="marker.cx"
              [attr.cy]="markerLaneY"
              [attr.r]="markerRadius"
              [attr.fill]="severityColor(marker.anomaly.severity)"
              data-test="anomaly-marker-dot"
            >
              <title>{{ markerTitle(marker.anomaly) }}</title>
            </circle>
            <circle
              class="marker-hit"
              [attr.cx]="marker.cx"
              [attr.cy]="markerLaneY"
              [attr.r]="markerHitRadius"
              fill="transparent"
            ></circle>
          </g>
        }

        @if (!domain()) {
          <text
            class="timeline-empty"
            [attr.x]="plotMidX"
            [attr.y]="plotMidY"
            text-anchor="middle"
            data-test="timeline-empty"
          >No samples in window</text>
        }
      </g>

      <text class="timeline-axis-caption" [attr.x]="plotLeft" [attr.y]="captionY">UTC</text>
    </svg>
  `,
  styles: [
    `
      :host {
        display: block;
        width: 100%;
      }

      .timeline-legend {
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem 1.25rem;
        margin-bottom: 0.5rem;
        font-size: 0.75rem;
        color: #94a3b8;
      }

      .legend-item {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
      }

      .legend-swatch {
        width: 0.65rem;
        height: 0.65rem;
        border-radius: 2px;
        flex: 0 0 auto;
      }

      .legend-range {
        color: #64748b;
        font-variant-numeric: tabular-nums;
      }

      .timeline-svg {
        display: block;
        width: 100%;
        height: auto;
        background: #0f172a;
        border: 1px solid #1e293b;
        border-radius: 6px;
      }

      .series-area {
        fill-opacity: 0.12;
        stroke: none;
      }

      .series-line {
        fill: none;
        stroke-width: 1.6;
        vector-effect: non-scaling-stroke;
      }

      .timeline-grid {
        stroke: #1e293b;
        stroke-width: 1;
      }

      .timeline-tick-label {
        fill: #64748b;
        font-size: 11px;
        font-variant-numeric: tabular-nums;
      }

      .timeline-axis-caption {
        fill: #475569;
        font-size: 10px;
      }

      .anomaly-marker {
        cursor: pointer;
      }

      .marker-stem {
        stroke: #334155;
        stroke-width: 1;
        stroke-dasharray: 3 4;
      }

      .marker-dot {
        stroke: #0f172a;
        stroke-width: 1.5;
      }

      .marker-hit {
        pointer-events: all;
      }

      .anomaly-marker:hover .marker-dot {
        stroke: #f1f5f9;
      }

      .timeline-empty {
        fill: #64748b;
        font-size: 13px;
      }
    `,
  ],
})
export class SvgTimelineComponent {
  /** Ordered history points from `GET /api/resources/history`. */
  readonly points = input<readonly HistoryPoint[]>([]);
  /** Anomaly markers from `GET /api/resources/anomalies`. */
  readonly anomalies = input<readonly Anomaly[]>([]);
  /** Enabled series keys (parent owns the toggle state). */
  readonly series = input<readonly TimelineSeriesKey[]>(DEFAULT_TIMELINE_SERIES);

  /** Emits the anomaly plus the padded `/traceback` window on marker click. */
  readonly markerClick = output<TimelineMarkerClick>();

  readonly viewBox = `0 0 ${TIMELINE_VIEW_WIDTH} ${TIMELINE_VIEW_HEIGHT}`;
  readonly plotTop = TIMELINE_PLOT_TOP;
  readonly plotBottom = TIMELINE_PLOT_BOTTOM;
  readonly plotLeft = TIMELINE_PLOT_LEFT;
  readonly markerLaneY = TIMELINE_MARKER_LANE_Y;
  readonly markerRadius = TIMELINE_MARKER_RADIUS;
  readonly markerHitRadius = TIMELINE_MARKER_HIT_RADIUS;
  readonly axisLabelY = TIMELINE_PLOT_BOTTOM + 20;
  readonly captionY = TIMELINE_VIEW_HEIGHT - 2;
  readonly plotMidX = TIMELINE_PLOT_LEFT + TIMELINE_PLOT_WIDTH / 2;
  readonly plotMidY = TIMELINE_PLOT_TOP + TIMELINE_PLOT_HEIGHT / 2;

  /** Time domain across points AND anomalies (markers stay on-canvas). */
  readonly domain = computed<{ t0: number; t1: number } | null>(() => {
    const times: number[] = [];
    for (const point of this.points()) {
      const ms = parseNaiveUtc(point.t).getTime();
      if (Number.isFinite(ms)) {
        times.push(ms);
      }
    }
    for (const anomaly of this.anomalies()) {
      for (const raw of [anomaly.t_start, anomaly.t_end]) {
        const ms = parseNaiveUtc(raw).getTime();
        if (Number.isFinite(ms)) {
          times.push(ms);
        }
      }
    }
    if (times.length === 0) {
      return null;
    }
    let t0 = Math.min(...times);
    let t1 = Math.max(...times);
    if (t1 === t0) {
      // A single timestamp still needs a non-degenerate domain.
      t0 -= 30_000;
      t1 += 30_000;
    }
    return { t0, t1 };
  });

  readonly seriesPaths = computed(() => {
    const domain = this.domain();
    const points = this.points();
    const enabled = this.series();

    return TIMELINE_SERIES_DEFS.filter((def) => enabled.includes(def.key)).map(
      (def) => {
        const values = points
          .map((point) => ({
            ms: parseNaiveUtc(point.t).getTime(),
            value: point[def.key],
          }))
          .filter((entry) => Number.isFinite(entry.ms) && Number.isFinite(entry.value));

        if (!domain || values.length === 0) {
          return {
            key: def.key,
            label: def.label,
            color: def.color,
            min: 0,
            max: 0,
            hasValues: false,
            line: '',
            area: '',
          };
        }

        const min = Math.min(...values.map((entry) => entry.value));
        const max = Math.max(...values.map((entry) => entry.value));
        const coords = values.map((entry) => ({
          x: timelineXScale(entry.ms, domain.t0, domain.t1),
          y: timelineYScale(entry.value, min, max),
        }));

        return {
          key: def.key,
          label: def.label,
          color: def.color,
          min,
          max,
          hasValues: true,
          line: buildLinePath(coords),
          area: buildAreaPath(coords),
        };
      },
    );
  });

  readonly ticks = computed(() => {
    const domain = this.domain();
    if (!domain) {
      return [];
    }
    const step = (domain.t1 - domain.t0) / (TIMELINE_TICK_COUNT - 1);
    const ticks: Array<{ x: number; label: string }> = [];
    for (let i = 0; i < TIMELINE_TICK_COUNT; i += 1) {
      const ms = domain.t0 + step * i;
      ticks.push({
        x: timelineXScale(ms, domain.t0, domain.t1),
        label: formatUtcClock(ms),
      });
    }
    return ticks;
  });

  readonly markers = computed(() => {
    const domain = this.domain();
    if (!domain) {
      return [];
    }
    return this.anomalies().map((anomaly) => ({
      anomaly,
      cx: timelineXScale(
        parseNaiveUtc(anomaly.t_start).getTime(),
        domain.t0,
        domain.t1,
      ),
    }));
  });

  severityColor(severity: AnomalySeverity): string {
    return SEVERITY_COLORS[severity] ?? SEVERITY_COLORS.medium;
  }

  markerTitle(anomaly: Anomaly): string {
    return `${anomaly.kind} · ${anomaly.shape} · ${anomaly.severity} · peak ${anomaly.peak}`;
  }

  onMarkerClick(anomaly: Anomaly): void {
    const { t0, t1 } = anomalyDrilldownWindow(anomaly);
    this.markerClick.emit({ anomaly, t0, t1 });
  }
}
