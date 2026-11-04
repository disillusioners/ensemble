/**
 * The RANGE + BASELINE DOMAIN (T1 phase 3, tasks 3.1 – 3.12).
 *
 * ── WHAT THIS MODULE IS ────────────────────────────────────────────────
 *
 * The pure half of the page frame: the window vocabulary, the keep-or-snap
 * resolution, the request builder, the server-anchored midpoint split and
 * the effective-interval derivation. No component, no DOM, no service — so
 * every rule below is testable on its own and the frame, the tab bodies and
 * the specs all read the SAME implementation.
 *
 * ── THE TWO VOCABULARIES, ONE VALUE ────────────────────────────────────
 *
 * The central door serves `15m / 30m / 1h / 2h`; the resources door serves
 * `15m / 1h / 24h / 7d`. The frame owns ONE window value — the union — and
 * the control that edits it is CENTRAL-SOURCED for the central tabs. The
 * custom tab's own ladder is a SEPARATE, labelled control on its panel
 * (AM-19); it edits the same value but maps it onto its own door.
 *
 * The option set is DERIVED from the manifest — the frame never owns a
 * second list of the windows (phase 2's coupling note).
 *
 * ── THE SPLIT IS ANCHORED ON THE RESPONSE, NEVER ON THE CLIENT CLOCK ───
 *
 * The baseline is a 2× window fetch split at the midpoint. The midpoint is
 * computed from the RESPONSE's own `window.from_utc` / `window.to_utc`
 * (parsed with the house `parseNaiveUtc`), because a 60 s client-clock skew
 * would misassign ~8 % of a 2 h window (AM-17). This module reads NO clock
 * at all — the split is a pure function of the payload.
 *
 * ── WHY NO CURSOR IS EVER MINTED ───────────────────────────────────────
 *
 * The door's cursor is unsigned base64 JSON, so a client COULD mint one to
 * shift the window. That is out of contract and brittle, so this module
 * exposes NO cursor constructor, and the pins spec proves the absence.
 */

import {
  MetricsPoint,
  MetricsSeriesEntry,
  MetricsSeriesPayload,
} from '../../models/metrics.model';
import { ResourcesWindow, parseNaiveUtc } from '../../models/resources.models';
import { METRICS_CENTRAL_WINDOW_MINUTES } from './metrics-door';
import {
  METRICS_CENTRAL_ENTRIES,
  METRICS_CUSTOM_ENTRIES,
  MetricsCentralWindow,
  MetricsTabEntry,
  metricsCustomTab,
} from './tabs.manifest';

/**
 * The frame's window VALUE — the union of the two vocabularies.
 *
 * It is one value, not two: the frame owns the range the operator picked,
 * and each slot maps it onto its own door (the central tabs pass
 * `window_s`; the custom panel passes the ladder name).
 */
export type MetricsRangeOption = MetricsCentralWindow | ResourcesWindow;

/**
 * The option set — the UNION of the manifest's per-tab `allowedWindows`.
 *
 * DERIVED, never declared: the central entries come first (their ladder is
 * the ascending one), then the custom-only members, so the rendered order
 * is ascending by duration without a second list.
 */
export const METRICS_RANGE_OPTIONS: readonly MetricsRangeOption[] = (() => {
  const seen = new Set<MetricsRangeOption>();
  const out: MetricsRangeOption[] = [];
  for (const tab of [...METRICS_CENTRAL_ENTRIES, ...METRICS_CUSTOM_ENTRIES]) {
    for (const option of tab.allowedWindows) {
      if (!seen.has(option)) {
        seen.add(option);
        out.push(option);
      }
    }
  }
  return out;
})();

/**
 * The SUBSTRATE CEILING — the door's own window target.
 *
 * The central door SILENTLY clamps a longer request
 * (`daemon/routers/metrics.py:778` — `max(1, min(window_s, WINDOW_SECONDS))`)
 * and answers 200 with no marker, so the request builder must never emit a
 * window above it. The value is DERIVED from the ladder (minutes × 60) so
 * no literal window second-count ever appears in the metrics tree (AM-23 —
 * phase 4's residue check greps the metrics tree for the word-bounded hour-window
 * literal, so no phase may carry one here).
 */
export const METRICS_WINDOW_CEILING_S = METRICS_CENTRAL_WINDOW_MINUTES['2h'] * 60;

/**
 * The BASELINE's guard — the largest window a baseline may ride.
 *
 * The baseline fetches 2W, so 2W must fit under the ceiling: W ≤ 1 h.
 */
export const METRICS_BASELINE_MAX_WINDOW_S = METRICS_CENTRAL_WINDOW_MINUTES['1h'] * 60;

/**
 * The span tolerance of the clamped-payload guard.
 *
 * The door's window anchor is emitted at SECOND precision, so a 2W payload
 * can be a fraction of a second short of exactly 2W. One second of slack
 * absorbs that without letting a genuinely clamped payload through.
 */
export const METRICS_BASELINE_SPAN_TOLERANCE_S = 1;

/**
 * The CENTRAL ladder's seconds for an option — `null` for a custom-only
 * option (the custom door takes the ladder NAME, never seconds).
 */
export function metricsCentralWindowSeconds(option: MetricsRangeOption): number | null {
  const minutes = (METRICS_CENTRAL_WINDOW_MINUTES as Readonly<
    Record<string, number | undefined>
  >)[option];
  return minutes === undefined ? null : minutes * 60;
}

/**
 * Whether the ACTIVE tab can serve an option.
 *
 * The visible enforcement (3.1): an option the active tab cannot serve is
 * rendered DISABLED rather than silently clamped by the door.
 */
export function metricsRangeEnabledFor(option: MetricsRangeOption, tab: MetricsTabEntry): boolean {
  return (tab.allowedWindows as readonly MetricsRangeOption[]).includes(option);
}

/** The outcome of a tab switch — the window to hold, and whether it moved. */
export interface MetricsWindowResolution {
  /** The window the frame holds after the switch. */
  readonly window: MetricsRangeOption;
  /** Whether the window MOVED (the snap). */
  readonly snapped: boolean;
}

/**
 * The switch-on-tab-change rule (3.2): keep the window when the new tab
 * supports it, otherwise SNAP to that tab's `defaultWindowS`.
 *
 * A snap is never silent — the caller announces it (a silent snap is a lie
 * about what is on screen).
 */
export function resolveWindowOnTabChange(
  current: MetricsRangeOption,
  tab: MetricsTabEntry,
): MetricsWindowResolution {
  if (metricsRangeEnabledFor(current, tab)) {
    return { window: current, snapped: false };
  }
  return { window: tab.defaultWindowS as MetricsRangeOption, snapped: true };
}

/**
 * The REQUEST BUILDER (3.5b) — the `window_s` the central tab asks for.
 *
 * The baseline SHARES the tab's single series request (3.10's invariant):
 * the baseline is not a second request, it is the SAME request over 2W,
 * split client-side. The builder therefore NEVER emits a window above the
 * substrate ceiling, whatever the caller asks for.
 */
export function metricsSeriesWindowS(windowS: number, baseline: boolean): number {
  const wanted = baseline ? windowS * 2 : windowS;
  if (!Number.isFinite(wanted) || wanted <= 0) {
    return METRICS_WINDOW_CEILING_S;
  }
  return Math.min(wanted, METRICS_WINDOW_CEILING_S);
}

/** The split of one payload at its own midpoint. */
export interface MetricsBaselineSplit {
  /** The CURRENT half — the points at or after the midpoint. */
  readonly current: readonly MetricsSeriesEntry[];
  /** The PREVIOUS half — the points before the midpoint. */
  readonly previous: readonly MetricsSeriesEntry[];
  /** The split instant, in epoch milliseconds — anchored on the RESPONSE. */
  readonly midpointMs: number;
  /** The response's window span, in seconds. */
  readonly spanS: number;
}

/**
 * The server-anchored midpoint split (3.4).
 *
 * The midpoint comes from the RESPONSE's own window anchors — `from_utc`
 * and `to_utc` — parsed with the house `parseNaiveUtc`. The client clock
 * is NEVER read, so a skewed client splits at the same instant as a
 * correct one.
 *
 * `null` means "there is no honest split": the payload's span is shorter
 * than 2W, i.e. the door CLAMPED the request (it answers 200 with the
 * ceiling's window and no marker). Splitting such a payload would show two
 * half-length windows mislabelled "previous window" (3.5c).
 */
export function splitSeriesAtMidpoint(
  payload: MetricsSeriesPayload,
  windowS: number,
): MetricsBaselineSplit | null {
  const fromMs = parseNaiveUtc(payload.window.from_utc).getTime();
  const toMs = parseNaiveUtc(payload.window.to_utc).getTime();
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) {
    return null;
  }
  const spanS = (toMs - fromMs) / 1000;
  if (spanS + METRICS_BASELINE_SPAN_TOLERANCE_S < windowS * 2) {
    // A CLAMPED payload — the door served less than the 2W we asked for.
    return null;
  }
  const midpointMs = fromMs + (toMs - fromMs) / 2;
  const current: MetricsSeriesEntry[] = [];
  const previous: MetricsSeriesEntry[] = [];
  for (const entry of payload.series) {
    const headPoints: MetricsPoint[] = [];
    const tailPoints: MetricsPoint[] = [];
    for (const point of entry.points) {
      if (point[0] * 1000 >= midpointMs) {
        headPoints.push(point);
      } else {
        tailPoints.push(point);
      }
    }
    current.push({ ...entry, points: headPoints });
    previous.push({ ...entry, points: tailPoints });
  }
  return { current, previous, midpointMs, spanS };
}

/**
 * The CUSTOM ladder's value for an option — `null` when the option is not
 * on the resources ladder.
 *
 * The ladder is the MANIFEST's own custom vocabulary (a compile-time
 * subset of the frozen `ResourcesWindow` union, phase 2's task 2.12), so
 * the mapping can never carry a value the resources door would 400 on.
 */
export function metricsResourcesWindow(option: MetricsRangeOption): ResourcesWindow | null {
  const ladder = metricsCustomTab().allowedWindows as readonly MetricsRangeOption[];
  return ladder.includes(option) ? (option as ResourcesWindow) : null;
}

/**
 * The EFFECTIVE point spacing, DERIVED from the payload (3.11 / AM-18).
 *
 * The door's `interval_s` is the OBSERVED sampler/publisher cadence; the
 * plotted points can be much further apart (at the 2 h option the ring's
 * points are bucket-max-downsampled to the payload's point budget). The
 * honest number is `span ÷ point count` — the spacing of the points that
 * are actually on screen.
 *
 * The count is the payload's DENSEST series: a series with fewer points is
 * spaced more widely, and the panel's own axis shows that.
 */
export function metricsDerivedIntervalS(payload: MetricsSeriesPayload | null): number | null {
  if (!payload) {
    return null;
  }
  const fromMs = parseNaiveUtc(payload.window.from_utc).getTime();
  const toMs = parseNaiveUtc(payload.window.to_utc).getTime();
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) {
    return null;
  }
  const spanS = (toMs - fromMs) / 1000;
  let points = 0;
  for (const entry of payload.series) {
    if (entry.points.length > points) {
      points = entry.points.length;
    }
  }
  if (points <= 0) {
    return null;
  }
  return spanS / points;
}

/** The count of the points the payload carries — the meta line's companion. */
export function metricsPayloadPointCount(payload: MetricsSeriesPayload | null): number {
  if (!payload) {
    return 0;
  }
  let points = 0;
  for (const entry of payload.series) {
    if (entry.points.length > points) {
      points = entry.points.length;
    }
  }
  return points;
}

// ── THE COPY (the frame's states) ───────────────────────────────────────

/**
 * The CUSTOM slot's baseline reason (3.6 / AM-14).
 *
 * The resources door serves a fixed ladder and `/api/resources/history`
 * takes only `window` + `resolution` — a previous window is NOT fetchable
 * there, and a client-side split of one payload would yield two HALF-length
 * windows mislabelled "previous window".
 */
export const METRICS_BASELINE_CUSTOM_SLOT_REASON =
  'the resources door serves a fixed 15m/1h/24h/7d vocabulary with no previous-window offset';

/**
 * The BASELINE's range reason (3.5a) — the toggle auto-clears when the
 * range crosses 1 h, and it says why.
 */
export const METRICS_BASELINE_RANGE_REASON =
  'the previous-window comparison needs a range of 1 h or less — the baseline was cleared';

/**
 * The CLAMPED-PAYLOAD reason (3.5c) — the door served less than the 2W the
 * baseline asked for, so no baseline is rendered.
 */
export const METRICS_BASELINE_CLAMPED_REASON =
  'the door clamped the window — the previous window is unavailable, so no baseline is shown';

/**
 * The FIDELITY DISCLOSURE (3.8) — the limits of the client-side split, in
 * the UI (never in a code comment).
 */
export const METRICS_BASELINE_FIDELITY_DISCLOSURE =
  'the baseline is downsampled over the whole fetched window — a bucket can straddle the split, ' +
  'the older half sits at the ring\'s eviction edge, and the resolution halves';

/**
 * The FRAME's range note — why the frame's range control is inert on the
 * custom tab (the panel carries its own labelled control — AM-19).
 */
export const METRICS_RANGE_CUSTOM_SLOT_NOTE =
  'the Resources panel has its own range control';

/**
 * The snap announcement (3.2) — a one-line meta note, never a silent snap.
 */
export function metricsSnapNote(option: MetricsRangeOption, tabLabel: string): string {
  return `range snapped to ${option} — ${tabLabel} does not serve the previous range`;
}
