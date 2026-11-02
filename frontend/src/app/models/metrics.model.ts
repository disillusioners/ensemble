/**
 * The metrics-query wire contract — a VERBATIM mirror of the FROZEN door
 * (`docs/architecture/metrics-query-contract.md`, the P1 artefact).
 *
 * House rule (D4 / `decisions.md`): every site quotes the canonical
 * family shape TOKEN-IDENTICALLY. The single source is
 * `.agents/shared/planning/central-metrics-v1/plan-overview.md`
 * §"the frozen contract":
 *
 *   {id, names:[{name, kind, labels:{key:[values]}|null, help, buckets:[…]|null, cardinality_class, monotonic}], live_series_count, availability:{state:"live"|"dark", reason?}, role, source_idiom, durability, cardinality_contribution}
 *
 * The ONE additive member beyond that string is the per-name
 * `chart_role` — contract §4.3 calls it "an **additive per-name
 * member** under the AM-7 bump policy (no `schema_version` bump;
 * consumers ignore unknown members)". The door serves it
 * (`provider.py`: `metric.effective_chart_role()`), and task 5.3 reads
 * it for the STEP/LINE rendering decision.
 *
 * Typing honesty (AM-4 / AM-7):
 *   - `vocabulary_version` is a `number` — NEVER a literal (the
 *     label-vocabulary axis moves independently of the envelope shape);
 *   - the availability reason is a WIDENED union with an
 *     `unknown(<value>)` fallback, so a reason the door adds later can
 *     never break the FE compile (P5-M9 pins this);
 *   - `kind` is `gauge | counter | histogram` — there is NO `summary`
 *     (the registry never produces one).
 *
 * Timestamps are AWARE UTC (AM-5: the door emits
 * `timestamps.now_utc_iso()`), and the FE parses them with the SAME
 * house helper the resources page uses — `parseNaiveUtc`
 * (`models/resources.models.ts`), which accepts a string that already
 * carries a zone designator and only appends `Z` to a zone-less one.
 * No second variant, no new helper.
 */

/** The metric kinds the registry produces (AM-4 — no `summary`). */
export type MetricsMetricKind = 'gauge' | 'counter' | 'histogram';

/**
 * The rendering mode of a series (roster task 2.7b): counters render
 * STEP, gauges render LINEAR. A linear interpolation between counter
 * samples would assert values that were never observed (data honesty).
 */
export type MetricsChartRole = 'line' | 'step';

/** The family-level chart role (FR-C2): chart-primary vs stat-primary. */
export type MetricsFamilyRole = 'chart' | 'stat';

/** The cardinality class of a registered name (AM-2). */
export type MetricsCardinalityClass = 'closed' | 'open' | 'none';

/** Where a family's values come from (registration-time metadata). */
export type MetricsSourceIdiom = 'registry' | 'postgres_census' | 'wedge_capture';

/** Whether a family's values survive a restart. */
export type MetricsDurability = 'process' | 'durable';

/** The availability state of a family. */
export type MetricsAvailabilityState = 'live' | 'dark';

/**
 * The v1 CLOSED availability reason set. Each reason is reachable by
 * exactly one runtime fact (contract §5).
 */
export type MetricsKnownAvailabilityReason =
  | 'census_gated'
  | 'census_not_running'
  | 'capture_off'
  | 'series_absent'
  | 'listener_disabled';

/**
 * AM-7 — the availability reason is a WIDENED union, NOT a closed
 * literal set: `(string & {})` keeps the literal autocompletion while
 * accepting any other string, so a reason the door adds later never
 * breaks the FE compile. `formatAvailabilityReason` renders an
 * unrecognised reason as `unknown(<value>)`.
 *
 * P5-M9 pins this: narrowing the union to the closed set must turn the
 * FE type-check leg RED (the spec assigns a not-yet-known reason).
 */
export type MetricsAvailabilityReason =
  | MetricsKnownAvailabilityReason
  | (string & {});

/** The label set of a series — the complete discriminator (AM-1). */
export interface MetricsLabels {
  readonly [key: string]: string;
}

/** The posture block — the same on all three routes (AM-18). */
export interface MetricsActivation {
  /** Process fact: the exposition socket is bound. */
  readonly listener_enabled: boolean;
  /** `not census.gated` — the honest switch-derived bit. */
  readonly census_enabled: boolean;
  /** The door's own posture — always `auth-gated-always-on`. */
  readonly query_api: string;
}

/** The envelope header — shared by all three routes (contract §4.1). */
export interface MetricsEnvelope {
  /** The envelope-SHAPE axis (an integer; bumped on a breaking change). */
  readonly schema_version: number;
  /** The label-VOCABULARY axis — a `number`, never a literal. */
  readonly vocabulary_version: number;
  /** Aware-UTC ISO-8601 (AM-5). */
  readonly generated_at_utc: string;
  /** The boot id — a restart is visible, never a silent gap (AM-14). */
  readonly restart_epoch: string;
  readonly activation: MetricsActivation;
}

/** One registered name inside a family. */
export interface MetricsFamilyName {
  readonly name: string;
  readonly kind: MetricsMetricKind;
  /** The declared label VALUES — `null` for an open-ended name (AM-2). */
  readonly labels: MetricsLabels | null;
  readonly help: string;
  /** The bucket boundaries — `null` for a non-histogram kind (B3). */
  readonly buckets: readonly number[] | null;
  readonly cardinality_class: MetricsCardinalityClass;
  readonly monotonic: boolean;
  /** The additive per-name rendering mode (contract §4.3). */
  readonly chart_role: MetricsChartRole;
}

/** Why a family is dark (the honest signal — never a fabricated zero). */
export interface MetricsAvailability {
  readonly state: MetricsAvailabilityState;
  readonly reason?: MetricsAvailabilityReason;
}

/** One family — the canonical frozen shape (see the header comment). */
export interface MetricsFamily {
  readonly id: string;
  readonly names: readonly MetricsFamilyName[];
  readonly live_series_count: number;
  readonly availability: MetricsAvailability;
  readonly role: MetricsFamilyRole;
  readonly source_idiom: MetricsSourceIdiom;
  readonly durability: MetricsDurability;
  readonly cardinality_contribution: number;
}

/** `GET /api/metrics/families`. */
export interface MetricsFamiliesPayload extends MetricsEnvelope {
  readonly families: readonly MetricsFamily[];
  /** More families remain after this page — the cursor resumes them. */
  readonly truncated: boolean;
}

/** The stored histogram of a histogram series (as stored, not cumulative). */
export interface MetricsHistogram {
  readonly count: number;
  readonly sum: number;
  readonly buckets: readonly number[];
}

/** One series of the snapshot — the CURRENT published value. */
export interface MetricsSnapshotSeries {
  readonly name: string;
  readonly labels: MetricsLabels;
  readonly kind: MetricsMetricKind;
  readonly monotonic: boolean;
  /** `null` for a histogram (the value lives in `histogram`). */
  readonly value: number | null;
  /** Seconds since the metric's last publish — staleness is explicit. */
  readonly last_publish_age_s: number;
  readonly histogram?: MetricsHistogram;
}

/** `GET /api/metrics/snapshot`. */
export interface MetricsSnapshotPayload extends MetricsEnvelope {
  readonly series: readonly MetricsSnapshotSeries[];
}

/** The window the trend was evaluated over (contract §4.5). */
export interface MetricsSeriesWindow {
  readonly from_utc: string;
  readonly to_utc: string;
  /** The OBSERVED effective spacing — never the configured cadence. */
  readonly interval_s: number;
  readonly interval_s_source: string;
}

/** A trend point — `[epoch-seconds, value]`, exactly as the door serves it. */
export type MetricsPoint = readonly [number, number];

/** One series of the trend. */
export interface MetricsSeriesEntry {
  readonly name: string;
  readonly labels: MetricsLabels;
  readonly kind: MetricsMetricKind;
  readonly monotonic: boolean;
  readonly points: readonly MetricsPoint[];
}

/** `GET /api/metrics/series`. */
export interface MetricsSeriesPayload extends MetricsEnvelope {
  readonly window: MetricsSeriesWindow;
  readonly last_sample_age_s: number;
  /** True when the newest sample is older than 3× the effective interval. */
  readonly stale: boolean;
  readonly series: readonly MetricsSeriesEntry[];
}

/** The placeholder a nullable scalar renders as (task 5.1). */
export const METRICS_EMPTY_VALUE = '—';

/**
 * The rendering mode of a series when the catalog does not name one:
 * a counter renders STEP, everything else renders LINEAR — the same
 * derivation the door makes (`checkpoint_metrics.py`
 * `effective_chart_role`).
 */
export function metricsChartRoleForKind(kind: MetricsMetricKind): MetricsChartRole {
  return kind === 'counter' ? 'step' : 'line';
}

/** A nullable scalar renders as the em-dash placeholder. */
export function formatMetricsValue(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return METRICS_EMPTY_VALUE;
  }
  return String(value);
}

/**
 * The human label of an availability reason. An UNRECOGNISED reason
 * renders as `unknown(<value>)` (AM-7) — the FE never invents a
 * meaning for a reason it does not know, and never hides it.
 */
export function formatAvailabilityReason(reason: string | null | undefined): string {
  if (reason === null || reason === undefined || reason === '') {
    return METRICS_EMPTY_VALUE;
  }
  switch (reason) {
    case 'census_gated':
      return 'the census sampler is gated';
    case 'census_not_running':
      return 'the census sampler is not running';
    case 'capture_off':
      return 'the wedge capture is not active';
    case 'series_absent':
      return 'no observation published yet';
    case 'listener_disabled':
      return 'the exposition listener is not bound';
    default:
      return `unknown(${reason})`;
  }
}

/** The label set of a series as a sorted `key=value` string. */
export function formatMetricsLabels(labels: MetricsLabels | null | undefined): string {
  if (!labels) {
    return '';
  }
  return Object.keys(labels)
    .sort()
    .map((key) => `${key}=${labels[key]}`)
    .join(' ');
}
