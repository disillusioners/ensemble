// The house chart THEME — the single palette + the format vocabulary.
//
// ONE source (T1 phase 1, task 1.5): the series palette was duplicated
// across `pages/metrics/charts/charts-tab.component.ts` and
// `pages/resources/svg-timeline/svg-timeline.component.ts`, and the
// "no value" token was duplicated across the chart sites. This module is
// the SSOT; the consumers import from it.
//
// BEHAVIOUR-NEUTRAL at this phase: the extraction is a MOVE, never a
// change. Every value here is byte-identical to the value it replaces.
//
// SCOPE NOTE — the chart sites do NOT share one UTC/value formatter: the
// SVG primitive's `formatChartUtc` guards a non-finite input ('—'), the
// timeline's `formatUtcClock` does not (it throws on an invalid date).
// Merging them would be a behaviour change, which this phase forbids, so
// the formatters stay where they are. The metric-kind → render-mode /
// unit-group vocabulary is Phase-4 material (IMPL-10) and does not exist
// yet — there is nothing to extract.

/**
 * The house series palette — the colour of the Nth series.
 *
 * The order is load-bearing: the timeline's five series take the FIRST
 * five entries, so a palette change moves both consumers together.
 */
export const CHART_SERIES_COLORS: readonly string[] = [
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
 * The placeholder a value-less sample renders as — the chart sites' own
 * "no value" token (the metrics model's `METRICS_EMPTY_VALUE` is the
 * same glyph; the chart primitive does not depend on the model barrel).
 */
export const CHART_EMPTY_VALUE = '—';
