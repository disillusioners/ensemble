import { Anomaly, parseNaiveUtc } from '../../../models/resources.models';

/**
 * The DRILL-DOWN POLICY (T1 phase 4, task 4.2).
 *
 * The padded drill-down window is POLICY, not geometry: it is about the
 * `/traceback` request's bounds, not about how a chart is drawn. The
 * consolidation therefore MOVED it OUT of the chart engine and into the
 * timeline's own module — where the tab that owns the drill-down can
 * read it.
 *
 * It is still an exported, unit-asserted helper (the port inventory
 * enumerates it by name); it simply lives with its owner now.
 */

/** Emitted on a marker click — the traceback drill-down window. */
export interface TimelineMarkerClick {
  readonly anomaly: Anomaly;
  /** `t_start − pad`, ISO-8601 UTC (aware). */
  readonly t0: string;
  /** `t_end + pad`, ISO-8601 UTC (aware). */
  readonly t1: string;
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
