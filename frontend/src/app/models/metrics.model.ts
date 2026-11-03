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

/**
 * The CONCRETE label set of a series — the complete discriminator
 * (AM-1). This is what `/snapshot` and `/series` carry: one entry per
 * LIVE label combination, every value a string (the runtime observation
 * §3.2 — e.g. `{pool: "native", quantity: "checked_out"}`).
 */
export interface MetricsLabels {
  readonly [key: string]: string;
}

/**
 * The DECLARED label VALUE SETS of a registered name (W-6). This is
 * what `/families` carries on `names[].labels` — the contract's frozen
 * `labels:{key:[values]}|null` shape (§4.3) and what the provider
 * serves (`_declared_labels` → `{key: sorted allowed values}`). A
 * closed name declares the full allowed set (e.g.
 * `{op: ["aget", "aget_tuple"]}`); an `open` or label-less name
 * declares `null` (AM-2). It is NOT a concrete labelling — rendering a
 * declared set as `op=aget,aget_tuple` presents a whole vocabulary as
 * if it were one observed value.
 */
export interface MetricsDeclaredLabels {
  readonly [key: string]: readonly string[];
}

/** The posture block — the same on all three routes (AM-18). */
export interface MetricsActivation {
  /** Process fact: the exposition socket is bound. */
  readonly listener_enabled: boolean;
  /** `not census.gated` — the honest switch-derived bit. */
  readonly census_enabled: boolean;
  /** The door's own posture — always `auth-gated-always-on`. */
  readonly query_api: string;
  /**
   * Whether the LAST registry read SUCCEEDED (the door's degraded-read
   * flag, H5).
   *
   * `false` means the registry read RAISED — the door then answers a
   * **200 with an EMPTY body**, byte-identical to a legitimately empty
   * registry, so this flag is the ONLY way a client can tell the two
   * apart.
   *
   * **OPTIONAL and DEFAULT-SAFE**: absent reads as healthy. The backend
   * always emits it, but a consumer must IGNORE unknown members (AM-7),
   * so the failure predicate is `=== false` — never a truthiness check.
   */
  readonly registry_read_ok?: boolean;
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
  /**
   * The DECLARED label VALUE SETS (W-6) — `null` for an open or
   * label-less name (AM-2). List-valued: the closed vocabulary of the
   * name, never a concrete observation.
   */
  readonly labels: MetricsDeclaredLabels | null;
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
  /**
   * The continuation cursor — PRESENT ONLY when the body is bounded
   * (the door pops the member when unbounded, `_bound_page`), so it is
   * optional in the mirror. Opaque: never parsed by the FE.
   */
  readonly next_cursor?: string;
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
  /**
   * The W-4 bound flag — the route is bounded like the other two
   * (offset/limit clamped to 500 + the byte ceiling). ADDITIVE.
   */
  readonly truncated: boolean;
  /** Present ONLY when the body is bounded (see `MetricsFamiliesPayload`). */
  readonly next_cursor?: string;
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
  /**
   * The W-2 bound flag — the page is bounded on TWO axes (the emitted
   * series ceiling AND the total emitted-points ceiling). A bound hit
   * is never silent: this flag is true and the cursor follows.
   */
  readonly truncated: boolean;
  /** Present ONLY when the body is bounded (see `MetricsFamiliesPayload`). */
  readonly next_cursor?: string;
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

/**
 * The DECLARED label value sets as a sorted string (W-6).
 *
 * A declared set is a VOCABULARY, not an observation, so the values
 * render as a set — `op∈{aget, aget_tuple}` — never as the concrete
 * `key=value` pair `formatMetricsLabels` renders (a declared set passed
 * to that helper would string-ify the array into `op=aget,aget_tuple`
 * and present the whole vocabulary as one observed value).
 */
export function formatMetricsDeclaredLabels(
  labels: MetricsDeclaredLabels | null | undefined,
): string {
  if (!labels) {
    return '';
  }
  return Object.keys(labels)
    .sort()
    .map((key) => {
      const values = [...labels[key]].sort().join(', ');
      return `${key}∈{${values}}`;
    })
    .join(' ');
}

// ── The door's error vocabulary → human copy (A-5, contract §5) ─────────

/**
 * The decoded shape of a failed metrics-door read (A-5). The door's
 * error vocabulary is CLOSED and frozen (contract §5): 401 / 403
 * scope / 400 invalid_label_filter | invalid_cursor / 404
 * unknown_family / 503 AUTH_NOT_CONFIGURED | registry_unavailable.
 * The shipped 403 body token is UPPERCASE `SCOPE_INSUFFICIENT` (D-c:
 * the doc gets amended, never the wire).
 */
export type MetricsDoorErrorCode =
  | 'scope_insufficient'
  | 'auth_not_configured'
  | 'registry_unavailable'
  | 'network'
  | 'unexpected';

/**
 * The copy for a DEGRADED registry read (`activation.registry_read_ok`
 * is `false`).
 *
 * GENERIC by design: the copy asserts only the FACT the flag carries —
 * the read failed, so the catalog below is INCOMPLETE or UNKNOWN, not
 * "no data". It never attributes a CAUSE (no defect naming): a shipped
 * string that names one goes stale the moment the owning lane lands,
 * and the failure mode it must not become is "the badge says 'empty'".
 *
 * It is deliberately NOT `registry_unavailable` — that copy means "no
 * provider wired" (the 503 case), and a failed registry READ is a
 * different fact.
 */
export const METRICS_REGISTRY_DEGRADED_COPY =
  'The metrics registry read failed — the catalog below is INCOMPLETE or UNKNOWN, not "no data".';

/** The copy the page banners for each decoded failure class (A-5). */
export const METRICS_DOOR_ERROR_COPY: Readonly<Record<MetricsDoorErrorCode, string>> = {
  scope_insufficient:
    'The metrics door rejected this session — the signed-in token lacks the maintenance:read scope.',
  auth_not_configured:
    'The metrics door is fail-closed — auth is not configured on the daemon, so it serves no metrics.',
  registry_unavailable:
    'The metrics door has no provider wired — the metrics service is unavailable on this daemon.',
  network: 'The metrics door is unreachable.',
  unexpected: 'The metrics door returned an unexpected error.',
};

/** Structural read of an `HttpErrorResponse`-like value (no Angular import — the model stays dependency-free). */
function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/**
 * Decode a failed door read into `{code, copy}` (A-5).
 *
 * The BODY is decoded FIRST and the status last — the house precedent
 * (`mcps-gateway.service.ts` `decodeError`): a 403 whose body names
 * `SCOPE_INSUFFICIENT` is a scope failure, and any OTHER 403 (CSRF
 * guard, reverse proxy) must NOT wear that label. `503` branches on
 * the body code (`AUTH_NOT_CONFIGURED` vs `registry_unavailable`).
 * `status === 0` is the transport never answering (network/timeout).
 * Everything else — 401, 400, 404, a proxied HTML page — is the
 * honest fallback, never the "unreachable" claim.
 */
export function decodeMetricsDoorError(err: unknown): {
  code: MetricsDoorErrorCode;
  copy: string;
} {
  const response = asRecord(err);
  const status = typeof response?.['status'] === 'number' ? (response['status'] as number) : 0;
  const body = asRecord(response?.['error']);
  const detail = asRecord(body?.['detail']) ?? body ?? {};
  const rawCode = asString(detail['error']) ?? asString(body?.['error']);

  if (rawCode === 'SCOPE_INSUFFICIENT' || (status === 403 && rawCode !== null)) {
    return { code: 'scope_insufficient', copy: METRICS_DOOR_ERROR_COPY.scope_insufficient };
  }
  if (status === 503) {
    if (rawCode === 'AUTH_NOT_CONFIGURED') {
      return { code: 'auth_not_configured', copy: METRICS_DOOR_ERROR_COPY.auth_not_configured };
    }
    if (rawCode === 'registry_unavailable') {
      return { code: 'registry_unavailable', copy: METRICS_DOOR_ERROR_COPY.registry_unavailable };
    }
    return { code: 'unexpected', copy: METRICS_DOOR_ERROR_COPY.unexpected };
  }
  if (status === 0) {
    return { code: 'network', copy: METRICS_DOOR_ERROR_COPY.network };
  }
  return { code: 'unexpected', copy: METRICS_DOOR_ERROR_COPY.unexpected };
}
