/**
 * The PER-SOURCE state model (T1 phase 2, task 2.11 — AM-1 / IMPL-09).
 *
 * ── WHY THE STATE IS PER SOURCE, NOT PER FRAME ───────────────────────
 *
 * The frame used to hold ONE global state, so a failing source blanked
 * every surface — including the ONE tab that still had live data. Under
 * the default install posture (auth OFF ⇒ the central door answers 503
 * while `/api/resources/*` answers 200) that blanked the only lit tab.
 *
 * The seam is the manifest's `central | custom` slot:
 *
 *   * a `custom` slot renders **its own source's** state;
 *   * a `central` slot renders **the central door's** state.
 *
 * So the state is held PER TAB, and each tab's state is written by its
 * OWN source. A failing central source can never blank the custom tab's
 * state, and a failing custom source can never blank a central tab's
 * state.
 *
 * The state object carries `{doorState, families, series, lastGood,
 * errorClass}` — the door class, the families the tab binds, the series
 * it published, the last-good payload (retained across a failure) and
 * the failure class.
 */

import {
  MetricsDoorErrorCode,
  MetricsFamily,
  MetricsSeriesEntry,
  MetricsSeriesPayload,
} from '../../models/metrics.model';

/**
 * The door-state class of a source.
 *
 * `ok` — the source answered; `loading` — it has not answered yet; every
 * other value is a failure class from the door's vocabulary (the
 * `timeout` class is the client-side deadline — the door's own answer
 * may still be in flight).
 */
export type MetricsDoorState = 'ok' | 'loading' | MetricsDoorErrorCode;

/** The PER-TAB state (AM-1). */
export interface MetricsTabState {
  /** The door-state class of THIS tab's source. */
  readonly doorState: MetricsDoorState;
  /** The families this tab binds (projected from the catalog). */
  readonly families: readonly MetricsFamily[];
  /**
   * The series this tab published.
   *
   * With the baseline ON this is the CURRENT half of the 2W payload — the
   * panels show the recent window, never the whole fetch.
   */
  readonly series: readonly MetricsSeriesEntry[];
  /**
   * The PREVIOUS window's series — the baseline's second dataset (3.4).
   *
   * It is EMPTY unless the baseline is on AND the payload was not clamped;
   * a clamped payload publishes no baseline and states why
   * (`baselineRefusal`).
   */
  readonly baselineSeries?: readonly MetricsSeriesEntry[];
  /**
   * Why NO baseline is rendered — the clamped-payload refusal (3.5c).
   *
   * `null` while the baseline renders normally.
   */
  readonly baselineRefusal?: string | null;
  /** The last-good payload — retained across a failure, never cleared. */
  readonly lastGood: MetricsSeriesPayload | null;
  /** The failure class — `null` while healthy. */
  readonly errorClass: MetricsDoorErrorCode | null;
}

/** The state a tab starts in — nothing is known yet. */
export const EMPTY_METRICS_TAB_STATE: MetricsTabState = {
  doorState: 'loading',
  families: [],
  series: [],
  baselineSeries: [],
  baselineRefusal: null,
  lastGood: null,
  errorClass: null,
};

/**
 * The reporter a tab body hands its OWN source's outcome to.
 *
 * The frame applies the patch to ONE tab's state — a body can never
 * write another tab's state, which is what makes the per-source
 * isolation structural rather than a convention.
 */
export type MetricsTabStateReporter = (patch: Partial<MetricsTabState>) => void;
