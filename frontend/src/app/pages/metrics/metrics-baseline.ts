/**
 * The FRAME → RENDERER BASELINE CONTRACT (T1 phase 3, task 3.7).
 *
 * ── WHAT THIS FILE IS ──────────────────────────────────────────────────
 *
 * A DECLARATION, not an implementation. The baseline is a RENDERER
 * CAPABILITY: the frame produces the previous window's series and hands
 * them to the renderer, which draws them as a second dataset over the
 * current one. Phase 4 implements the renderer side; this phase fixes the
 * SHAPE and says which renderer is which.
 *
 * ── WHICH RENDERER IS WHICH (AM-9 / R-2) ───────────────────────────────
 *
 *   * the **Chart.js panel** is the NORMAL path — the baseline is a second
 *     dataset over the previous window;
 *   * the **house SVG** carries the baseline as FALLBACK capability (its
 *     optional baseline line) — exercised through the mount-failure
 *     fallback and the renderer specs;
 *   * the **mini / sparkline** role has NO page surface in T1 — the
 *     capability is retained, not surfaced.
 *
 * ── THE SHAPE ──────────────────────────────────────────────────────────
 *
 * The dataset is the SAME shape the panels already render (epoch-ms `t`,
 * numeric `v`), so a baseline series is a drop-in second dataset: the
 * renderer draws it with the panel's own scale, and the frame never has to
 * know how a renderer draws anything.
 */

/** One baseline series — the shape a renderer draws as its second dataset. */
export interface MetricsBaselineSeries {
  /** The stable identity of the series (the `data-series` hook). */
  readonly id: string;
  /** The human label — the renderer draws it in the legend. */
  readonly label: string;
  /** The samples — epoch-ms `t`, numeric `v`. */
  readonly points: readonly { readonly t: number; readonly v: number }[];
}

/**
 * The CONTRACT — the declared wiring, in one place.
 *
 * It is a frozen literal so a spec can assert it verbatim: the renderer
 * path, the fallback path, the mini role's absence, and the rule that the
 * baseline NEVER adds a request of its own.
 */
export const METRICS_BASELINE_CONTRACT = {
  /** The renderer that draws the T1 baseline on the NORMAL path. */
  normalPath: 'chart.js-panel',
  /** The renderer's fallback capability — the house SVG's baseline line. */
  fallbackPath: 'house-svg-baseline',
  /** The mini role has no page surface in T1. */
  miniRolePageSurface: false,
  /**
   * The baseline SHARES the tab's single series request — it is never a
   * second request (3.10's invariant).
   */
  addsRequest: false,
  /** The split is anchored on the RESPONSE's window, never the client clock. */
  splitAnchor: 'response-window',
} as const;

/**
 * The frame's baseline dataset for one panel — the previous window's
 * series, ready for a renderer.
 *
 * The frame hands the renderer SERIES, never pixels: the renderer owns the
 * drawing, the frame owns the data.
 */
export function metricsBaselineDataset(
  series: readonly { readonly name: string; readonly labels: Record<string, string>; readonly points: readonly (readonly [number, number])[] }[],
  labelSuffix = 'previous window',
): MetricsBaselineSeries[] {
  return series.map((entry) => {
    const label = Object.keys(entry.labels).length
      ? `${entry.name} {${Object.entries(entry.labels)
          .map(([key, value]) => `${key}=${value}`)
          .join(',')}}`
      : entry.name;
    return {
      id: `${label} — ${labelSuffix}`,
      label: `${label} (${labelSuffix})`,
      points: entry.points.map((point) => ({ t: point[0] * 1000, v: point[1] })),
    };
  });
}
