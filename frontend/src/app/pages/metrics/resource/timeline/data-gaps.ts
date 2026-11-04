import { HistoryPoint, parseNaiveUtc } from '../../../../models/resources.models';
import { TIMELINE_SERIES_DEFS, TimelineSeriesKey } from './timeline-series';

/**
 * The CLIENT-SIDE DATA-GAP ANNOTATION (T1 phase 5, task 5.8 — ADJ-iv shape 2).
 *
 * ── THE DERIVATION IS THE /history PAYLOAD, NOTHING ELSE ───────────────
 *
 * No restart or deploy seam exists in ANY `/api/resources/*` endpoint,
 * and the anomaly journal is process-local (it resets per daemon
 * restart). The ONLY honest annotation the FE can derive is a DATA GAP
 * read off the `/history` discontinuity itself: a series that DROPS TO
 * ZERO or to a MISSING SAMPLE (a `null` value) and then REGROWS.
 *
 * The label therefore names only what the FE derived —
 * "DATA GAP — series unavailable in this interval" — and NEVER
 * "restart", "deploy" or "promote". A planted "restart" wording FAILS
 * the spec (`data-gaps.spec.ts` plants it, watches the spec go RED, and
 * restores it).
 *
 * A trailing unavailable stretch never regrows and is NOT annotated:
 * without a regrowth there is no discontinuity to name — it may be the
 * window's leading/trailing edge, not a gap.
 */
export const DATA_GAP_LABEL = 'DATA GAP — series unavailable in this interval';

/** One derived data gap — a series interval the payload says was unavailable. */
export interface ResourceDataGap {
  readonly seriesKey: TimelineSeriesKey;
  readonly seriesLabel: string;
  /** The annotation text — the fixed label, the series, and the window. */
  readonly text: string;
  /** The first unavailable sample, epoch ms. */
  readonly fromMs: number;
  /** The sample where the series REGROWS, epoch ms. */
  readonly toMs: number;
}

/**
 * Detect the data gaps of a `/history` payload (5.8).
 *
 * For every timeline series, a maximal run of UNAVAILABLE samples (a
 * `null` value or a zero value) that has a known-good sample BEFORE it
 * and a regrowth AFTER it is one gap. Runs without a regrowth are the
 * window's edges and are never annotated.
 */
export function detectResourceDataGaps(points: readonly HistoryPoint[]): ResourceDataGap[] {
  const out: ResourceDataGap[] = [];
  for (const def of TIMELINE_SERIES_DEFS) {
    let gapStartMs: number | null = null;
    let hadGoodSample = false;
    for (const point of points) {
      const tMs = parseNaiveUtc(point.t).getTime();
      if (!Number.isFinite(tMs)) {
        continue;
      }
      const value: number | null = point[def.key];
      const unavailable = value === null || value === 0;
      if (unavailable) {
        // A gap OPENS only behind a known-good sample — a leading
        // unavailable stretch is the window's edge, not a gap.
        if (gapStartMs === null && hadGoodSample) {
          gapStartMs = tMs;
        }
        continue;
      }
      if (gapStartMs !== null) {
        // The series REGREW — the gap closes here.
        out.push({
          seriesKey: def.key,
          seriesLabel: def.label,
          text: `${DATA_GAP_LABEL} · ${def.label} · ${utcClock(gapStartMs)}–${utcClock(tMs)} UTC`,
          fromMs: gapStartMs,
          toMs: tMs,
        });
        gapStartMs = null;
      }
      hadGoodSample = true;
    }
    // A trailing unavailable stretch never regrows — not annotated.
  }
  return out;
}

/** `HH:MM` UTC clock label for an epoch-ms instant (the chip's window). */
function utcClock(ms: number): string {
  return new Date(ms).toISOString().slice(11, 16);
}
