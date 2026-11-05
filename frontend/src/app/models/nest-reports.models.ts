/**
 * The WIRE TYPES + the DOMAIN-LOCAL ERROR COPY of the nest-reports
 * domain route (`/api/nest-reports/*` — the nest-anomaly lane, phase 4).
 *
 * ── WHY A DOMAIN MODEL, NOT A METRICS MODEL ─────────────────────────────
 *
 * The incident list is ROW-shaped (clusters grouped by tree root ×
 * category, plus their member reports) — deliberately NOT a second
 * metrics query path (the lane's R8 ruling: the metrics door stays
 * FROZEN, the rows get their own route with the SAME auth posture).
 * These types mirror what `daemon/routers/nest_reports.py` actually
 * serves — ONE projector, two transports (the route and the
 * `nest-report-read` tools call the same service methods, so the shapes
 * cannot drift).
 *
 * ── THE VOCABULARY RULE (R1 / R3 — binding) ─────────────────────────────
 *
 * The FE carries NO category set and NO severity set:
 *
 *   * `category` is the RAW stored canonical string, rendered verbatim;
 *   * the severity chip tiers by the route's `severity_rank` INTEGER
 *     (1 = minor, 2 = major, 3 = critical) — the mapping lives
 *     server-side (`SEVERITY_RANK`), never in a second FE copy;
 *   * the chip's TEXT is the route's `severity_label` — the PEAK
 *     phrasing ("critical (peak)", AM-7), rendered verbatim;
 *   * `open` is DERIVED SERVER-SIDE (`last_report_at >= now −
 *     resolve_window_seconds`, the R5 resolve window; default 1h) —
 *     the FE renders the boolean and never recomputes it (a client
 *     clock would disagree with the server's window).
 */

import { decodeMetricsDoorError } from './metrics.model';

/**
 * The bound block — the response's own accounting (six members).
 *
 * Present on BOTH payloads. `truncated` is true when the page was
 * bounded (the FE renders the `narrow_hint` verbatim when set).
 */
export interface AnomalyBound {
  readonly matched_count: number;
  readonly returned_count: number;
  readonly dropped_count: number;
  readonly more_available: boolean;
  readonly truncated: boolean;
  readonly narrow_hint: string | null;
}

/**
 * The freshness block — the SERVER's query instant and window.
 *
 * The FE renders `line` VERBATIM (the server's own sentence names the
 * window, the instant and the resolve rule — the house rule: never the
 * client clock).
 */
export interface AnomalyFreshness {
  readonly window_s: number;
  readonly window_cutoff_utc: string;
  readonly queried_at_utc: string;
  readonly resolve_window_s: number;
  readonly line: string;
}

/**
 * ONE incident GROUP — one row of the list, grouped SERVER-SIDE by
 * `(tree_root_instance_id, canonical category)` (AM-6c).
 *
 * The grouping is the ROUTE's, never the FE's: this tab renders the
 * groups verbatim and NEVER re-keys, re-windows or merges them
 * client-side. The occurrence badge is the number of CLUSTERS in the
 * group (not reports — a cluster aggregates many reports), and
 * `cluster_ids` carries the member cluster ids so a group expansion
 * needs NO extra call.
 *
 * NAMING RECONCILIATION (the plan's Task 3 names this type
 * `AnomalyCluster`; phase 3's route serves GROUPS): the alias below
 * keeps the plan's name bound to the GROUP shape the wire actually
 * carries.
 */
export interface AnomalyClusterGroup {
  readonly tree_root_instance_id: string;
  /** The RAW stored canonical category — rendered verbatim (R1). */
  readonly category: string;
  /** The group's PEAK severity (the max-rank rollup). */
  readonly severity: string;
  /** The PEAK phrasing, e.g. "critical (peak)" (AM-7) — rendered verbatim. */
  readonly severity_label: string;
  /** 1 = minor, 2 = major, 3 = critical — the FE's ONLY severity input. */
  readonly severity_rank: number;
  /** The SUMMED report count across the group's clusters. */
  readonly report_count: number;
  /** The number of CLUSTERS in the group (the occurrence badge, AM-6c). */
  readonly occurrence_count: number;
  /** The member cluster ids (a group expansion needs no extra call). */
  readonly cluster_ids: readonly string[];
  readonly last_report_at: string;
  /** DERIVED SERVER-SIDE (the R5 resolve window) — rendered, never recomputed. */
  readonly open: boolean;
  /** Present ONLY when at least one member cluster filed a board item. */
  readonly board_item_ids?: readonly string[];
}

/** The plan's `AnomalyCluster` name — the GROUP shape the wire carries. */
export type AnomalyCluster = AnomalyClusterGroup;

/** `GET /api/nest-reports/clusters` — the grouped incident list. */
export interface AnomalyClustersPayload {
  readonly groups: readonly AnomalyClusterGroup[];
  readonly bound: AnomalyBound;
  readonly category_breakdown: Readonly<
    Record<string, { readonly groups: number; readonly reports: number }>
  >;
  readonly freshness: AnomalyFreshness;
  readonly generated_at_utc: string;
  /** Present ONLY when `bound.more_available` (the resume token). */
  readonly cursor?: string;
}

/**
 * ONE member report of a cluster (the detail read).
 *
 * `excerpt` appears ONLY when the call carried `include_excerpt=true`
 * (AM-12b — the default detail payload carries no excerpt anywhere);
 * when present the FE renders at most 300 characters with a reveal
 * affordance (AM-12c), never raw.
 */
export interface AnomalyClusterReport {
  readonly report_id: string;
  readonly agent_id: string;
  readonly instance_id: string;
  readonly created_at: string;
  readonly category: string;
  readonly category_raw: string | null;
  readonly severity: string;
  readonly description: string;
  readonly outcome: string;
  readonly excerpt?: string;
}

/** `GET /api/nest-reports/clusters/{cluster_id}` — one cluster's members. */
export interface AnomalyClusterDetailPayload {
  readonly cluster_id: string;
  readonly category: string;
  readonly severity: string;
  readonly severity_rank: number;
  readonly severity_label: string;
  readonly report_count: number;
  readonly tree_root_instance_id: string;
  readonly first_report_at: string;
  readonly last_report_at: string;
  readonly open: boolean;
  readonly board_item_id: string | null;
  readonly reports: readonly AnomalyClusterReport[];
  readonly bound: AnomalyBound;
  readonly freshness: {
    readonly queried_at_utc: string;
    readonly resolve_window_s: number;
    readonly line: string;
  };
  readonly generated_at_utc: string;
}

/**
 * ── THE ERROR VOCABULARY — the honest mapping (Task 3, binding) ────────
 *
 * The route's closed vocabulary: `400 bad_request` · `401` (the auth
 * middleware) · `403 SCOPE_INSUFFICIENT` · `404
 * anomaly_cluster_not_found` · `503 AUTH_NOT_CONFIGURED` · `503
 * anomaly_unavailable`.
 *
 * SIX of those outcomes are the SAME FACTS the metrics door states, so
 * they reuse the shared door-state classes verbatim (rendered through
 * the shared `app-metrics-door-state`, whose copy SSOT says what
 * happened and what to do). The frozen 10-class door union
 * (`MetricsDoorErrorCode`) is NOT widened.
 *
 * TWO outcomes are DOMAIN-ONLY — the door's copy would be a FALSE
 * statement for them (the door's `unknown_family` copy claims "this
 * page is ahead of the daemon", meaningless for a cluster id; its
 * `registry_unavailable` copy claims "no provider wired", meaningless
 * for the anomaly substrate). They carry the domain-local copy below.
 */
export type NestReportsReusableErrorCode = Extract<
  | 'auth_not_configured'
  | 'scope_insufficient'
  | 'auth_required'
  | 'bad_request'
  | 'timeout'
  | 'network',
  string
>;

/** The domain-only outcomes — the two the door's copy cannot state honestly. */
export type NestReportsDomainErrorCode =
  | 'cluster_not_found'
  | 'anomaly_unavailable'
  | 'unexpected';

/** Every outcome the nest-reports route's failure can decode to. */
export type NestReportsErrorCode =
  | NestReportsReusableErrorCode
  | NestReportsDomainErrorCode;

/**
 * The domain panel's banner — a DISCRIMINATED shape, not a bare code.
 *
 * The shared `app-metrics-door-state` accepts a DOOR class only, and
 * the domain-only classes are NOT door classes (the frozen door union
 * is never widened). The shape lets the template branch on the KIND
 * and hand the shared panel a code it can actually render.
 */
export type NestReportsBanner =
  | { readonly kind: 'door'; readonly code: NestReportsReusableErrorCode }
  | { readonly kind: 'domain'; readonly code: NestReportsDomainErrorCode };

/** The structured copy of a DOMAIN-ONLY outcome (the door-state shape). */
export interface NestReportsDomainCopy {
  readonly cause: string;
  readonly meaning: string;
  readonly action: string;
  readonly pointer: string | null;
  readonly retryable: boolean;
}

/**
 * The DOMAIN-LOCAL copy table — exhaustive over the domain-only
 * outcomes, no silent fall-through (the models spec pins every class
 * has exactly one row).
 */
export const NEST_REPORTS_DOMAIN_COPY: Readonly<
  Record<NestReportsDomainErrorCode, NestReportsDomainCopy>
> = {
  cluster_not_found: {
    cause: 'This cluster is no longer known — it may have been pruned.',
    meaning: 'The retention sweep removed it after the list was read.',
    action: 'Collapse the row and reload the incident list.',
    pointer: null,
    retryable: false,
  },
  anomaly_unavailable: {
    cause: 'The anomaly store is unavailable on this daemon.',
    meaning: 'The route answered a service failure — no incident rows were read.',
    action: 'Retry the read; a store that stays unavailable needs an operator.',
    pointer: null,
    retryable: true,
  },
  unexpected: {
    cause: 'The nest-reports route returned an unexpected error.',
    meaning: 'The failure matches no class the route documents.',
    action: 'Retry the read; a repeated failure needs the response body.',
    pointer: null,
    retryable: true,
  },
};

/**
 * Decode a failed nest-reports read into its outcome class.
 *
 * The mapping is the plan's Task 3 table, verbatim:
 *
 *   | the route's answer          | the FE renders                |
 *   |-----------------------------|-------------------------------|
 *   | 503 AUTH_NOT_CONFIGURED     | `auth_not_configured` (door)  |
 *   | 403 SCOPE_INSUFFICIENT      | `scope_insufficient` (door)   |
 *   | 401                         | `auth_required` (door)        |
 *   | 400                         | `bad_request` (door)          |
 *   | client deadline             | `timeout` (door)              |
 *   | transport (status 0)        | `network` (door)              |
 *   | 404                         | `cluster_not_found` (domain)  |
 *   | 503 anomaly_unavailable     | `anomaly_unavailable` (domain)|
 *   | anything else               | `unexpected` (domain)         |
 *
 * The `unexpected` fallback is DOMAIN-LOCAL on purpose: the door's
 * `unexpected` copy says "the metrics door returned…", a false subject
 * for this route — the no-silent-fall-through rule needs a row whose
 * SUBJECT is the nest-reports route.
 *
 * The four classes the door already states are REUSED from the house
 * decoder (ONE status table, never two).
 */
export function decodeNestReportsError(err: unknown): NestReportsErrorCode {
  const status = statusOf(err);
  const rawCode = rawErrorCode(err);
  // The 404 and the 503 branches are the DOMAIN outcomes — the door's
  // own classes would state the wrong thing for them.
  if (status === 404) {
    return 'cluster_not_found';
  }
  if (status === 503) {
    if (rawCode === 'AUTH_NOT_CONFIGURED') {
      return 'auth_not_configured';
    }
    if (rawCode === 'anomaly_unavailable') {
      return 'anomaly_unavailable';
    }
    return 'unexpected';
  }
  if (status === 403) {
    return rawCode !== null && rawCode.toUpperCase() === 'SCOPE_INSUFFICIENT'
      ? 'scope_insufficient'
      : 'unexpected';
  }
  const door = decodeMetricsDoorError(err).code;
  if (
    door === 'auth_required' ||
    door === 'bad_request' ||
    door === 'timeout' ||
    door === 'network'
  ) {
    return door;
  }
  return 'unexpected';
}

/** The HTTP status of a failed read (0 when there is no response). */
function statusOf(err: unknown): number {
  if (typeof err !== 'object' || err === null) {
    return 0;
  }
  const status = (err as Record<string, unknown>)['status'];
  return typeof status === 'number' ? status : 0;
}

/** The route's error token (`detail.error`), read structurally. */
function rawErrorCode(err: unknown): string | null {
  if (typeof err !== 'object' || err === null) {
    return null;
  }
  const response = err as Record<string, unknown>;
  const rawBody = response['error'];
  const body = (typeof rawBody === 'object' && rawBody !== null ? rawBody : {}) as Record<
    string,
    unknown
  >;
  const rawDetail = body['detail'];
  const detail = (typeof rawDetail === 'object' && rawDetail !== null ? rawDetail : body) as Record<
    string,
    unknown
  >;
  const candidate = detail['error'] ?? body['error'];
  return typeof candidate === 'string' && candidate.length > 0 ? candidate : null;
}
