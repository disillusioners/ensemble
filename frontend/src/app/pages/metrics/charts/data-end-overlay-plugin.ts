/**
 * The HOUSE DATA-END OVERLAY PLUGIN (T1 phase 8, task 8.10).
 *
 * ── WHAT IT DRAWS ───────────────────────────────────────────────────────
 *
 * A vertical line at the timestamp where the panel's data ENDS, with a
 * visible label beside it.
 *
 * **WHY IT EXISTS.** The audit found that a stale panel showed a line
 * that simply STOPPED, with nothing on the chart saying where it
 * stopped. A reader cannot tell "the value fell to zero" from "the
 * data ends here and nothing newer exists" — the two look identical on
 * the pixels. The data-end marker names the difference.
 *
 * **IT IS A HOUSE PLUGIN, NOT A VENDOR ONE** — the same constraint the
 * anomaly overlay obeys (IMPL-05: the lane's dependency set is frozen
 * at exactly two direct entries). A second vendor plugin would be a
 * second dependency.
 *
 * **IT IS DRAW-ONLY.** The marker carries no interaction: it says where
 * the data ends, and nothing else.
 */

/** The plugin's registration id (Chart.js keys its options by it). */
export const HOUSE_DATA_END_PLUGIN_ID = 'house-data-end-overlay';

/** The marker's line colour — the house's muted slate. */
export const HOUSE_DATA_END_COLOR = '#94a3b8';

/** The marker's label font — small, legible, canvas-pixel scale. */
export const HOUSE_DATA_END_LABEL_FONT = '10px system-ui, sans-serif';

/** The marker's label — the visible words beside the line. */
export const HOUSE_DATA_END_LABEL_PREFIX = 'data ends';

/**
 * The DATA END — where the panel's data stops.
 *
 * `ms` is the epoch milliseconds of the LAST plotted sample; `label` is
 * the visible text beside the line (the surface composes it — the house
 * never formats a time twice).
 */
export interface HouseDataEnd {
  readonly ms: number;
  readonly label: string;
}

/** The plugin's options — the data end it draws. */
export interface HouseDataEndPluginOptions {
  readonly dataEnd: HouseDataEnd | null;
}

/** The minimal chart surface the plugin needs (Chart.js is structural). */
interface HouseChartLike {
  readonly ctx: CanvasRenderingContext2D;
  readonly chartArea: { top: number; left: number; right: number; bottom: number };
  readonly scales: { x?: { getPixelForValue(value: number): number } };
}

/** The house plugin's shape (Chart.js's `Plugin`, structurally). */
export interface HouseDataEndPlugin {
  readonly id: string;
  afterDatasetsDraw(
    chart: HouseChartLike,
    args: unknown,
    options: HouseDataEndPluginOptions,
  ): void;
}

/**
 * The house data-end overlay plugin.
 *
 * The line is drawn AFTER the datasets, so it sits ON TOP of the plot
 * — a marker under the data would be invisible exactly when it matters
 * (a series that ends ON the line).
 */
export function createHouseDataEndOverlayPlugin(): HouseDataEndPlugin {
  return {
    id: HOUSE_DATA_END_PLUGIN_ID,
    afterDatasetsDraw(
      chart: HouseChartLike,
      _args: unknown,
      options: HouseDataEndPluginOptions,
    ): void {
      const dataEnd = options?.dataEnd ?? null;
      if (!dataEnd) {
        return;
      }
      const scale = chart.scales?.x;
      if (!scale) {
        return;
      }
      const x = scale.getPixelForValue(dataEnd.ms);
      if (!Number.isFinite(x)) {
        return;
      }
      const ctx = chart.ctx;
      const { top, bottom } = chart.chartArea;
      ctx.save();
      ctx.strokeStyle = HOUSE_DATA_END_COLOR;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      // The VISIBLE label — the marker must not exist only as a line:
      // the reader has to be told WHAT the line is.
      ctx.fillStyle = HOUSE_DATA_END_COLOR;
      ctx.font = HOUSE_DATA_END_LABEL_FONT;
      ctx.textAlign = 'right';
      ctx.fillText(dataEnd.label || HOUSE_DATA_END_LABEL_PREFIX, x - 4, top + 10);
      ctx.restore();
    },
  };
}
