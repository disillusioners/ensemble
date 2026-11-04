/**
 * The CONSOLIDATED HOUSE SVG ENGINE — the pure geometry + formatting
 * helpers (T1 phase 4, task 4.1).
 *
 * ── THE PORT, NOT A REWRITE (AM-26) ──────────────────────────────────────
 *
 * This module is the single home of BOTH former engines' exported
 * symbols. The newer engine (`svg-line-chart.component.ts`) contributes
 * TWELVE functions; the older engine (`svg-timeline.component.ts`)
 * contributes SIX more plus the marker geometry. Every one of those
 * names is still EXPORTED here and still UNIT-ASSERTED — that property
 * is exactly why the source audit could cite them by name.
 *
 * The consolidation has ONE geometry. The older engine's `TIMELINE_*`
 * constants are therefore ALIASES onto the house constants, and the
 * spec pins that aliasing (the single-geometry invariant). The
 * assertions that referenced the old constants are RE-POINTED, never
 * silently dropped — the per-assertion disposition table
 * (`phase4-evidence.md`) accounts for every one.
 *
 * The ONE symbol that does NOT live here is `anomalyDrilldownWindow`:
 * it is drill-down POLICY, not geometry (task 4.2), so it moved OUT of
 * the chart into the timeline tab's own policy module
 * (`pages/metrics/resource/timeline/timeline-drilldown.ts`). It stays exported
 * and unit-asserted — there, not here.
 *
 * ── WHAT THIS MODULE IS NOT ──────────────────────────────────────────────
 *
 * It owns no HTTP, no polling and no component state. Every function is
 * pure, so the geometry is unit-testable without a DOM (the house
 * `svg-timeline:88-114` pattern). The Angular component that draws with
 * these helpers lives beside this file.
 *
 * ── THE HOUSE RULES THE GEOMETRY HONOURS ─────────────────────────────────
 *
 *   * **THE SHARED PANEL DOMAIN** (phase 6, task 6.1 / W7 — replaces the
 *     old per-series normalisation) — every series handed to the engine
 *     is ONE panel's worth (a panel is a metric-kind + unit group), and
 *     every series is plotted against THAT PANEL'S single shared
 *     [min,max] (`chartSharedDomain`). **No series is ever
 *     independently rescaled within a panel** — a per-series rescale
 *     would let a small-magnitude series fake a full-height swing inside
 *     a panel whose axis says otherwise. The legend still prints each
 *     series' OWN observed range (the values are per-series facts; the
 *     AXIS is the panel's).
 *   * **COUNTER-HONESTY** — a counter renders STEP (`H`/`V`), never a
 *     diagonal: a linear interpolation between counter samples would
 *     assert values that were never observed.
 *   * **UTC-NEVER-LOCAL** — every clock label is derived from the
 *     epoch-ms instant via `toISOString()`, never from the process
 *     timezone.
 */

import { CHART_EMPTY_VALUE } from '../chart-theme';

// ── Geometry (ONE viewBox; the text is never stretched) ─────────────────

/** The house viewBox width. */
export const CHART_VIEW_WIDTH = 900;
/** The house viewBox height. */
export const CHART_VIEW_HEIGHT = 260;
/** The plot's left edge. */
export const CHART_PLOT_LEFT = 56;
/** The plot's right edge. */
export const CHART_PLOT_RIGHT = CHART_VIEW_WIDTH - 16;
/** The plot's top edge. */
export const CHART_PLOT_TOP = 18;
/** The plot's bottom edge. */
export const CHART_PLOT_BOTTOM = CHART_VIEW_HEIGHT - 34;
/** The plot's width. */
export const CHART_PLOT_WIDTH = CHART_PLOT_RIGHT - CHART_PLOT_LEFT;
/** The plot's height. */
export const CHART_PLOT_HEIGHT = CHART_PLOT_BOTTOM - CHART_PLOT_TOP;
/** The number of time ticks across the window. */
export const CHART_TICK_COUNT = 5;
/** The radius of a sample marker. */
export const CHART_POINT_RADIUS = 2.2;

/**
 * The stroke width at `devicePixelRatio === 1`.
 *
 * The MINI extension's crispness rule divides this by the ratio, so a
 * stroke always covers a whole number of DEVICE pixels (a 2× display
 * gets a 0.8-unit stroke, which is 1.6 device pixels — never a
 * half-pixel seam).
 */
export const CHART_STROKE_WIDTH = 1.6;

/** The anomaly marker's dot radius. */
export const TIMELINE_MARKER_RADIUS = 6;

/**
 * The house GEOMETRY — the coordinate system the helpers project into.
 *
 * The house defaults are the constants above. The MINI extension's
 * `viewBox` responsiveness needs a SECOND geometry (a container-shaped
 * one), so the geometry is a VALUE the helpers take as an optional
 * trailing argument: the ported call shapes are unchanged (the default
 * is the house geometry), and the component can adapt the box to its
 * container without a second copy of the projection maths.
 */
export interface HouseGeometry {
  readonly viewWidth: number;
  readonly viewHeight: number;
  readonly plotLeft: number;
  readonly plotRight: number;
  readonly plotTop: number;
  readonly plotBottom: number;
}

/** The house geometry — the single default of every helper. */
export const HOUSE_GEOMETRY: HouseGeometry = {
  viewWidth: CHART_VIEW_WIDTH,
  viewHeight: CHART_VIEW_HEIGHT,
  plotLeft: CHART_PLOT_LEFT,
  plotRight: CHART_PLOT_RIGHT,
  plotTop: CHART_PLOT_TOP,
  plotBottom: CHART_PLOT_BOTTOM,
};

/** The bottom margin the house geometry keeps below the plot. */
export const HOUSE_AXIS_MARGIN = CHART_VIEW_HEIGHT - CHART_PLOT_BOTTOM;

/**
 * The geometry for a measured container (the `viewBox` responsiveness).
 *
 * The viewBox keeps the house WIDTH and adopts the container's ASPECT,
 * so the drawing fills its container instead of being letterboxed. The
 * plot's bottom follows the viewBox's bottom margin, so the drawing is
 * never clipped and the text is never stretched (the whole viewBox
 * scales uniformly).
 *
 * A missing or nonsensical measurement falls back to the house
 * geometry — the renderer never draws into a zero-sized box.
 */
export function houseGeometryFor(
  measuredWidth: number,
  measuredHeight: number,
): HouseGeometry {
  if (!Number.isFinite(measuredWidth) || measuredWidth <= 0) {
    return HOUSE_GEOMETRY;
  }
  const height = Number.isFinite(measuredHeight) && measuredHeight > 0
    ? Math.round((HOUSE_GEOMETRY.viewWidth * measuredHeight) / measuredWidth)
    : HOUSE_GEOMETRY.viewHeight;
  const viewHeight = Math.max(HOUSE_GEOMETRY.viewHeight, Math.min(height, HOUSE_GEOMETRY.viewHeight * 2));
  return {
    viewWidth: HOUSE_GEOMETRY.viewWidth,
    viewHeight,
    plotLeft: HOUSE_GEOMETRY.plotLeft,
    plotRight: HOUSE_GEOMETRY.plotRight,
    plotTop: HOUSE_GEOMETRY.plotTop,
    plotBottom: viewHeight - HOUSE_AXIS_MARGIN,
  };
}

// ── Types ────────────────────────────────────────────────────────────────

/**
 * The rendering mode of one series.
 *
 * Structurally identical to the metrics wire's `MetricsChartRole`
 * (`'line' | 'step'`), but declared here so the shared house engine
 * does not depend on the metrics model barrel (the resource timeline
 * uses it too).
 */
export type HouseRenderMode = 'line' | 'step';

/** One sample — `t` is epoch MILLISECONDS, `v` the observed value. */
export interface ChartPoint {
  readonly t: number;
  readonly v: number;
}

/** One series — the engine's input shape. */
export interface ChartSeries {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  /** The rendering mode — `step` for counters, `line` for gauges. */
  readonly mode: HouseRenderMode;
  readonly points: readonly ChartPoint[];
}

/** A plot coordinate. */
export interface ChartCoord {
  readonly x: number;
  readonly y: number;
}

/** One time tick. */
export interface ChartTick {
  readonly x: number;
  readonly label: string;
  readonly ms: number;
}

/** One sample marker. */
export interface ChartMarker {
  readonly key: string;
  readonly x: number;
  readonly y: number;
  readonly title: string;
}

/**
 * ONE anomaly marker — the house engine's marker shape.
 *
 * The timestamp is epoch MILLISECONDS (the consumer converts its own
 * wire shape), so the shared engine never depends on the resources
 * model barrel.
 *
 * The `anomalyId` is OPTIONAL: a marker that only says "something
 * happened here" (a threshold crossing) has no anomaly record behind
 * it, and the payload then carries no id rather than a fabricated one.
 * The `seriesId` names the series the marker belongs to (the Chart.js
 * overlay resolves the marker against its datasets).
 */
export interface HouseAnomalyMarker {
  readonly anomalyId?: string;
  readonly seriesId: string;
  readonly tsMs: number;
  readonly severity: 'low' | 'medium' | 'high';
  readonly title: string;
}

/** A rendered series — the component's per-series projection. */
export interface ChartSeriesRender {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  readonly mode: HouseRenderMode;
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
export function chartXScale(
  ms: number,
  t0: number,
  t1: number,
  geometry: HouseGeometry = HOUSE_GEOMETRY,
): number {
  const width = geometry.plotRight - geometry.plotLeft;
  if (!Number.isFinite(ms)) {
    return geometry.plotLeft;
  }
  if (!(t1 > t0)) {
    return geometry.plotLeft + width / 2;
  }
  return geometry.plotLeft + clamp01((ms - t0) / (t1 - t0)) * width;
}

/**
 * Value → viewBox y (inverted), NORMALISED against the range the caller
 * passes — for the house engine that range is ALWAYS the panel's shared
 * domain (W7: no series is rescaled inside a panel). A value below
 * `min` clamps to the floor and a value above `max` clamps to the
 * ceiling — the axis never shows a value that was not observed.
 */
export function chartYScale(
  value: number,
  min: number,
  max: number,
  geometry: HouseGeometry = HOUSE_GEOMETRY,
): number {
  const height = geometry.plotBottom - geometry.plotTop;
  if (!Number.isFinite(value)) {
    return geometry.plotBottom;
  }
  if (!(max > min)) {
    return geometry.plotTop + height / 2;
  }
  return geometry.plotBottom - clamp01((value - min) / (max - min)) * height;
}

/** The observed [min,max] of a series (the per-series VALUES fact). */
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

/**
 * The SHARED PANEL DOMAIN (phase 6, task 6.1 / W7) — the observed
 * [min,max] across EVERY series of one panel (and its baseline).
 *
 * A panel is a metric-kind + unit group, so its series are commensurate
 * by construction and ONE domain is the honest axis: every series in
 * the panel is plotted against this single domain, and NO series is
 * independently rescaled. An empty panel degenerates to the zero range
 * (the caller renders the empty state, not the axis).
 */
export function chartSharedDomain(
  seriesList: readonly { readonly points: readonly ChartPoint[] }[],
): { min: number; max: number } {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const entry of seriesList) {
    for (const point of entry.points) {
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
  geometry: HouseGeometry = HOUSE_GEOMETRY,
): ChartCoord[] {
  const out: ChartCoord[] = [];
  for (const point of points) {
    if (!Number.isFinite(point.t) || !Number.isFinite(point.v)) {
      continue;
    }
    out.push({
      x: chartXScale(point.t, t0, t1, geometry),
      y: chartYScale(point.v, min, max, geometry),
    });
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
  mode: HouseRenderMode,
): string {
  return mode === 'step' ? buildChartStepPath(coords) : buildChartLinePath(coords);
}

/** The series path closed to the plot floor — the area fill. */
export function buildChartAreaPath(
  coords: readonly ChartCoord[],
  mode: HouseRenderMode,
  geometry: HouseGeometry = HOUSE_GEOMETRY,
): string {
  if (coords.length === 0) {
    return '';
  }
  const body = buildChartPath(coords, mode);
  const first = coords[0];
  const last = coords[coords.length - 1];
  return `${body} L${last.x.toFixed(2)},${geometry.plotBottom.toFixed(2)} L${first.x.toFixed(
    2,
  )},${geometry.plotBottom.toFixed(2)} Z`;
}

/** The UTC tick positions + labels across the window. */
export function chartUtcTicks(
  t0: number,
  t1: number,
  count: number = CHART_TICK_COUNT,
  geometry: HouseGeometry = HOUSE_GEOMETRY,
): ChartTick[] {
  if (!(t1 > t0) || count <= 0) {
    return [];
  }
  const out: ChartTick[] = [];
  for (let index = 0; index < count; index += 1) {
    const fraction = count === 1 ? 0 : index / (count - 1);
    const ms = t0 + (t1 - t0) * fraction;
    out.push({
      x: chartXScale(ms, t0, t1, geometry),
      label: formatChartUtcTick(ms, t1 - t0),
      ms,
    });
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

/**
 * The UTC tick label, DISAMBIGUATED BY THE WINDOW SPAN.
 *
 * A `HH:MM`-only label is ambiguous once the window crosses a day
 * boundary — two ticks a day apart would print the same text. A window
 * of 24 h or more therefore carries the UTC DATE (`MM-DD HH:MM`); a
 * shorter window keeps the compact `HH:MM`.
 *
 * The label is ALWAYS derived from the epoch-ms instant, so it is UTC
 * in every process timezone.
 */
export function formatChartUtcTick(ms: number, spanMs: number): string {
  if (!Number.isFinite(ms)) {
    return CHART_EMPTY_VALUE;
  }
  const iso = new Date(ms).toISOString();
  if (Number.isFinite(spanMs) && spanMs >= 24 * 60 * 60 * 1000) {
    return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
  }
  return iso.slice(11, 16);
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

// ── The MINI extensions (ADJ-ii — the CLOSED set) ────────────────────────

/**
 * The NEAREST sample index for a pointer position (the bisect).
 *
 * The overlay's pointer position is converted to a timestamp and the
 * nearest sample is found by bisecting the sorted sample times — a
 * linear scan over hundreds of samples per pointer move is the thing
 * the mini tier must not do.
 *
 * Returns `-1` for an empty series.
 */
export function houseNearestPointIndex(
  times: readonly number[],
  targetMs: number,
): number {
  if (times.length === 0 || !Number.isFinite(targetMs)) {
    return -1;
  }
  let low = 0;
  let high = times.length - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (times[mid] < targetMs) {
      low = mid + 1;
    } else {
      high = mid;
    }
  }
  // `low` is the first index at or after the target; the nearest is it
  // or its predecessor.
  if (low === 0) {
    return 0;
  }
  const before = times[low - 1];
  const after = low < times.length ? times[low] : before;
  if (low >= times.length) {
    return times.length - 1;
  }
  return targetMs - before <= after - targetMs ? low - 1 : low;
}

/**
 * The stroke width for a device pixel ratio (the crispness rule).
 *
 * The stroke is divided by the ratio, so it always covers a whole
 * number of device pixels. A non-finite or non-positive ratio falls
 * back to 1 (the reference density).
 */
export function houseCrispStrokeWidth(ratio: number): number {
  if (!Number.isFinite(ratio) || ratio <= 0) {
    return CHART_STROKE_WIDTH;
  }
  return CHART_STROKE_WIDTH / ratio;
}

/**
 * The responsive viewBox STRING for a geometry.
 *
 * The viewBox is the coordinate system the geometry lives in, so it is
 * derived from the geometry rather than from a second literal.
 */
export function houseViewBox(geometry: HouseGeometry = HOUSE_GEOMETRY): string {
  return `0 0 ${geometry.viewWidth} ${geometry.viewHeight}`;
}

// ── The OLDER engine's ported names (ALIASES onto the single geometry) ──

/**
 * The ported alias of `chartXScale` (the older engine's name).
 *
 * The older engine's geometry is GONE — the house engine has ONE
 * viewBox — so the alias points at the house scale. The name survives
 * so every ported assertion keeps a named home (AM-26).
 */
export const timelineXScale = chartXScale;

/** The ported alias of `chartYScale` (the older engine's name). */
export const timelineYScale = chartYScale;

/** The ported alias of `buildChartLinePath` (the older engine's name). */
export const buildLinePath = buildChartLinePath;

/**
 * The ported area-path helper (the older engine's name).
 *
 * The older engine's area started at the plot floor and walked the
 * points; the house area closes the house body to the floor. The house
 * form is the canonical one, so the ported helper delegates to it.
 */
export function buildAreaPath(coords: readonly ChartCoord[]): string {
  return buildChartAreaPath(coords, 'line');
}

/**
 * The ported UTC clock formatter (the older engine's name).
 *
 * The older engine's formatter threw on an invalid date; the house
 * formatter renders the em-dash placeholder. The house behaviour is
 * the one that survives the consolidation (a broken sample must not
 * take the whole chart down).
 */
export function formatUtcClock(ms: number): string {
  return formatChartUtc(ms);
}

// The three ported aliases that remain are the REFERENCED ones (their
// consumers are the renderer specs and the timeline geometry); the six
// zero-reference siblings were removed with the consolidation sweep.
/** The ported alias of the house plot left edge (one geometry). */
export const TIMELINE_PLOT_LEFT = CHART_PLOT_LEFT;
/** The ported alias of the house plot right edge (one geometry). */
export const TIMELINE_PLOT_RIGHT = CHART_PLOT_RIGHT;
/** The ported alias of the house tick count (one geometry). */
export const TIMELINE_TICK_COUNT = CHART_TICK_COUNT;
