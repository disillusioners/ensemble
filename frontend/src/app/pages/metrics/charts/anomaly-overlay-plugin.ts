import {
  ANOMALY_MARKER_RADIUS,
  CHART_SEVERITY_COLORS,
} from '../../../components/charts/chart-theme';
import { HouseAnomalyMarker } from '../../../components/charts/house-svg-renderer/house-svg-renderer';

/**
 * The HOUSE ANOMALY OVERLAY PLUGIN (T1 phase 4, task 4.7).
 *
 * **A house plugin, not a vendor one.** The obvious vendor choice —
 * `chartjs-plugin-annotation` — would be a SECOND dependency, and the
 * lane's dependency set is frozen at exactly two direct entries
 * (IMPL-05: `chart.js` + `jest-canvas-mock`). The overlay is therefore
 * written here, in the house's own code.
 *
 * ── WHAT IT DRAWS ───────────────────────────────────────────────────────
 *
 * For every marker it draws the house's anomaly marker: a dashed stem
 * down the plot and a severity-coloured dot in the marker lane above
 * it. The severity colours are the house palette
 * (`CHART_SEVERITY_COLORS`) — VERBATIM, the same values the retired
 * `svg-timeline.component.ts:169-173` used.
 *
 * The plugin is DRAW-ONLY: the click hand-off belongs to the wrapper
 * (it owns the `pointClick` payload), so the plugin exposes the marker
 * lookup helper the wrapper uses.
 */

/** The plugin's registration id (Chart.js keys its options by it). */
export const HOUSE_ANOMALY_PLUGIN_ID = 'house-anomaly-overlay';

/**
 * The marker radius, in canvas pixels — VALUE-sourced from the shared
 * `ANOMALY_MARKER_RADIUS` (chart-theme, the cross-stack parity token).
 * This name remains exported because the plugin's spec asserts against
 * it; the plugin body below reads the shared constant directly.
 */
export const HOUSE_ANOMALY_MARKER_RADIUS = ANOMALY_MARKER_RADIUS;

export type { HouseAnomalyMarker } from '../../../components/charts/house-svg-renderer/house-svg-renderer';

/** The plugin's options — the markers it draws. */
export interface HouseAnomalyPluginOptions {
  readonly markers: readonly HouseAnomalyMarker[];
}

/** The minimal chart surface the plugin needs (Chart.js is structural). */
interface HouseChartDataset {
  readonly label?: string;
  readonly seriesId?: string;
}

interface HouseChartLike {
  readonly ctx: CanvasRenderingContext2D;
  readonly chartArea: { top: number; left: number; right: number; bottom: number };
  readonly scales: { x?: { getPixelForValue(value: number): number } };
  readonly data: { datasets: HouseChartDataset[] };
}

/** The house plugin's shape (Chart.js's `Plugin`, structurally). */
export interface HouseAnomalyPlugin {
  readonly id: string;
  beforeDatasetsDraw(
    chart: HouseChartLike,
    args: unknown,
    options: HouseAnomalyPluginOptions,
  ): void;
}

/** The severity colour of a marker — the house palette, verbatim. */
export function houseAnomalyColor(severity: 'low' | 'medium' | 'high'): string {
  return CHART_SEVERITY_COLORS[severity] ?? CHART_SEVERITY_COLORS.medium;
}

/** The dataset index of a series (the `seriesId` wins over the label). */
export function houseDatasetIndex(
  datasets: readonly HouseChartDataset[],
  seriesId: string,
): number {
  return datasets.findIndex((dataset) => dataset.seriesId === seriesId);
}

/**
 * The marker nearest a click — the wrapper's `pointClick` payload.
 *
 * A marker is "near" when it belongs to the clicked series and sits
 * within `toleranceMs` of the clicked timestamp. The nearest wins.
 */
export function houseNearestAnomaly(
  markers: readonly HouseAnomalyMarker[],
  seriesId: string,
  tsMs: number,
  toleranceMs: number,
): HouseAnomalyMarker | null {
  let best: HouseAnomalyMarker | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const marker of markers) {
    if (marker.seriesId !== seriesId) {
      continue;
    }
    const distance = Math.abs(marker.tsMs - tsMs);
    if (distance > toleranceMs || distance >= bestDistance) {
      continue;
    }
    best = marker;
    bestDistance = distance;
  }
  return best;
}

/**
 * The house anomaly overlay plugin.
 *
 * The marker lane sits at the TOP of the chart area, so the markers
 * never cover the plotted samples.
 *
 * **The marker's label is VISIBLE (T1 phase 5, task 5.7 — ADJ-iv shape
 * 1).** The API words the endpoint already serves — `kind`, `severity`,
 * `series_key`, `peak` — are drawn as text beside the marker (ADJ-iv:
 * they must not exist only in a hover-only `<title>`). The words are
 * composed by the surface's label composer (`anomalyMarkerLabel`) and
 * travel on the marker's `title`; the plugin draws that title text
 * verbatim, so the marker itself carries the words.
 */
export function createHouseAnomalyOverlayPlugin(): HouseAnomalyPlugin {
  return {
    id: HOUSE_ANOMALY_PLUGIN_ID,
    beforeDatasetsDraw(
      chart: HouseChartLike,
      _args: unknown,
      options: HouseAnomalyPluginOptions,
    ): void {
      const markers = options?.markers ?? [];
      if (!markers.length) {
        return;
      }
      const scale = chart.scales?.x;
      if (!scale) {
        return;
      }
      const ctx = chart.ctx;
      const laneY = chart.chartArea.top + ANOMALY_MARKER_RADIUS;
      ctx.save();
      for (const marker of markers) {
        const index = houseDatasetIndex(chart.data.datasets, marker.seriesId);
        if (index < 0) {
          continue;
        }
        const x = scale.getPixelForValue(marker.tsMs);
        if (!Number.isFinite(x)) {
          continue;
        }
        ctx.fillStyle = houseAnomalyColor(marker.severity);
        ctx.beginPath();
        ctx.arc(x, laneY, ANOMALY_MARKER_RADIUS, 0, Math.PI * 2);
        ctx.fill();

        // 5.7 — the VISIBLE label: the marker's composed words, drawn
        // beside the dot in the marker lane (never over the samples).
        if (marker.title) {
          ctx.fillStyle = HOUSE_ANOMALY_LABEL_COLOR;
          ctx.font = HOUSE_ANOMALY_LABEL_FONT;
          ctx.fillText(marker.title, x + ANOMALY_MARKER_RADIUS + 4, laneY + 3);
        }
      }
      ctx.restore();
    },
  };
}

/** The visible label's muted slate colour (the house D5 text-secondary). */
export const HOUSE_ANOMALY_LABEL_COLOR = '#94a3b8';

/** The visible label's font — small, legible, canvas-pixel scale. */
export const HOUSE_ANOMALY_LABEL_FONT = '10px system-ui, sans-serif';
