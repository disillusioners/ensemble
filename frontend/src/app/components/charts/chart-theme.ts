// The house chart THEME — the single palette + the format vocabulary.
//
// ONE source (T1 phase 1, task 1.5): the series palette was duplicated
// across the charts tab and the resources timeline, and the "no value"
// token was duplicated across the chart sites. This module is the SSOT;
// the consumers import from it.
//
// The consumers are now the CONSOLIDATED house engine
// (`components/charts/house-svg-renderer/`) and the Chart.js panel
// (`pages/metrics/charts/chart-panel.component.ts`).
//
// BEHAVIOUR-NEUTRAL at this phase: the extraction is a MOVE, never a
// change. Every value here is byte-identical to the value it replaces.
//
// SCOPE NOTE — the consolidation (phase 4) MERGED the two UTC/value
// formatters: the house engine's `formatChartUtc` guards a non-finite
// input ('—'), and the retired timeline's `formatUtcClock` did not (it
// threw on an invalid date). The house (guarded) behaviour is the one
// that survives, so a broken sample can no longer take the whole chart
// down. The `formatUtcClock` NAME survives as an alias in the house
// engine, so every ported assertion keeps a named home.
//
// The metric-kind → render-mode / unit-group vocabulary is the metrics
// page's own module (`pages/metrics/metrics-units.ts`).

/**
 * The house series palette — the colour of the Nth series.
 *
 * The order is load-bearing: the timeline's five series take the FIRST
 * five entries, so a palette change moves both consumers together.
 */
const CHART_SERIES_COLORS: readonly string[] = [
  '#38bdf8',
  '#a78bfa',
  '#34d399',
  '#fbbf24',
  '#f472b6',
  '#60a5fa',
  '#fb923c',
  '#2dd4bf',
];

/** The colour of the Nth series — the palette wraps. */
export function chartSeriesColor(index: number): string {
  return CHART_SERIES_COLORS[index % CHART_SERIES_COLORS.length];
}

/**
 * The house anomaly-severity palette — the timeline's marker colours.
 *
 * The keys are the wire severities (low / medium / high); a severity the
 * FE does not know renders as `unknown`, never as a borrowed colour.
 */
export const CHART_SEVERITY_COLORS: Readonly<Record<'low' | 'medium' | 'high', string>> = {
  low: '#38bdf8',
  medium: '#f59e0b',
  high: '#ef4444',
};

/**
 * The anomaly-marker radius — ONE shared value so the Chart.js overlay
 * plugin and the house SVG timeline draw the SAME marker dot (cross-stack
 * visual parity is a VALUE coupling, not a coincidence). Both consumers
 * import this; neither carries its own copy.
 */
export const ANOMALY_MARKER_RADIUS = 6;

/**
 * The placeholder a value-less sample renders as — the chart sites' own
 * "no value" token (the metrics model's `METRICS_EMPTY_VALUE` is the
 * same glyph; the chart primitive does not depend on the model barrel).
 */
export const CHART_EMPTY_VALUE = '—';
