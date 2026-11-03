/**
 * The HOUSE CROSSHAIR PLUGIN (T1 phase 4, task 4.6).
 *
 * The crosshair is the vertical line at the SNAPPED index — the visual
 * half of the "crosshair + nearest-point snap" pair. The snap itself is
 * Chart.js's own `interaction: { mode: 'index', intersect: false }`,
 * which is what makes the pointer pick the nearest sample across every
 * dataset instead of the point under the cursor.
 *
 * Like the anomaly overlay, this is a HOUSE plugin: the lane's
 * dependency set is frozen at exactly two direct entries, so a vendor
 * crosshair package is not an option.
 */

/** The plugin's registration id. */
export const HOUSE_CROSSHAIR_PLUGIN_ID = 'house-crosshair';

/** The minimal chart surface the plugin needs (Chart.js is structural). */
interface HouseChartLike {
  readonly ctx: CanvasRenderingContext2D;
  readonly chartArea: { top: number; left: number; right: number; bottom: number };
  readonly tooltip?: {
    readonly _active?: readonly { element: { x: number; y: number } }[];
  };
}

/** The plugin's shape (Chart.js's `Plugin`, structurally). */
export interface HouseCrosshairPlugin {
  readonly id: string;
  afterDatasetsDraw(chart: HouseChartLike, args: unknown, options: unknown): void;
}

/**
 * The house crosshair plugin.
 *
 * The line is drawn from the chart area's top to its bottom, so it
 * spans the plot without covering the axis labels.
 */
export function createHouseCrosshairPlugin(): HouseCrosshairPlugin {
  return {
    id: HOUSE_CROSSHAIR_PLUGIN_ID,
    afterDatasetsDraw(chart: HouseChartLike, _args: unknown, _options: unknown): void {
      const active = chart.tooltip?._active ?? [];
      if (!active.length) {
        return;
      }
      const x = active[0]?.element?.x;
      if (!Number.isFinite(x)) {
        return;
      }
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.55)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, chart.chartArea.top);
      ctx.lineTo(x, chart.chartArea.bottom);
      ctx.stroke();
      ctx.restore();
    },
  };
}
