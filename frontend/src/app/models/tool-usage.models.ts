/**
 * The WIRE TYPES + the DOMAIN-LOCAL ERROR COPY of the tool-usage domain
 * route (`/api/tool-usage/*` — the tool-usage-metrics-p1 lane, phase 4;
 * ENSEMBLE-37 / TokenEconomizer adoption, Phase 1 measurement slice).
 *
 * ── THE FROZEN CONTRACT (phase1-plan §3.4 / §4 T8 — phase 4 CONSUMES) ───
 *
 *   GET /api/tool-usage/summary?project_id=<optional, omit=all-projects>&days=7
 *     → { generated_at, rows: [{ tool_name, tool_category, calls_24h,
 *         calls_7d, calls_total, agents: [{ agent_id, calls_7d }] }] }
 *
 *   GET /api/tool-usage/daily?project_id=&tool_name=&days=
 *     → { series: [{ day, calls }] }
 *
 * The field names here are typed AGAINST that contract verbatim; a BE
 * rename forces a model + service edit (the plan's §4.1 drift note).
 * The rows arrive UNSORTED — the FE sorts client-side by `calls_7d`
 * DESC (the ranking input), ties by `tool_name` asc.
 *
 * ── THE ERROR VOCABULARY — the honest mapping (phase4 Task 3) ──────────
 *
 * The route's closed vocabulary: `400 bad_request` · `401` (the auth
 * middleware) · `403 SCOPE_INSUFFICIENT` · `404 unknown_tool` · `503
 * AUTH_NOT_CONFIGURED` · `503 tool_usage_unavailable`.
 *
 * SIX of those outcomes are the SAME FACTS the metrics door states, so
 * they reuse the shared door-state classes verbatim (rendered through
 * the shared `app-metrics-door-state`, whose copy SSOT says what
 * happened and what to do). The frozen 10-class door union
 * (`MetricsDoorErrorCode`) is NOT widened.
 *
 * TWO outcomes are DOMAIN-ONLY — the door's copy would be a FALSE
 * statement for them (the door's `unknown_family` copy claims "this
 * page is ahead of the daemon", meaningless for a tool name; its
 * `registry_unavailable` copy claims "no provider wired", meaningless
 * for the tool-usage substrate). They carry the domain-local copy
 * below. The house no-silent-fall-through rule adds ONE more domain row
 * — `unexpected`, the closed fallback whose SUBJECT is this route (the
 * nest-reports precedent, `nest-reports.models.ts`) — so the copy table
 * is EXHAUSTIVE over the domain union with no silent fall-through.
 */

import { decodeMetricsDoorError } from './metrics.model';

/** ONE per-agent cell of a tool row — the frozen nested shape. */
export interface ToolUsageAgent {
  readonly agent_id: string;
  readonly calls_7d: number;
}

/** ONE row of the summary — one tool's ledger totals. */
export interface ToolUsageRow {
  readonly tool_name: string;
  /** `string | null` — the door may emit null when category resolution fails (tester I3). */
  readonly tool_category: string | null;
  /** The rolling today+ surface (vaguer by design — the D-7 disclosure). */
  readonly calls_24h: number;
  /** The ranking input — the FE sorts by this, DESCENDING. */
  readonly calls_7d: number;
  readonly calls_total: number;
  /** The per-agent breakdown (already in the payload — no second fetch). */
  readonly agents: readonly ToolUsageAgent[];
}

/** `GET /api/tool-usage/summary` — the summary payload. */
export interface ToolUsageSummary {
  /** The SERVER's UTC instant — never the client clock. */
  readonly generated_at: string;
  readonly rows: readonly ToolUsageRow[];
}

/** ONE point of the daily series — one UTC day (zero-filled by the BE). */
export interface ToolUsageDailyPoint {
  /** The UTC day, e.g. `2026-11-20`. */
  readonly day: string;
  readonly calls: number;
}

/** `GET /api/tool-usage/daily` — the daily payload (the trend chart). */
export interface ToolUsageDailySeries {
  readonly series: readonly ToolUsageDailyPoint[];
}

/**
 * ── THE ERROR CLASSES ─────────────────────────────────────────────────
 *
 * The door-reusable classes render through the shared
 * `app-metrics-door-state`; the domain-only classes render the local
 * copy table below. The union is CLOSED.
 */
export type ToolUsageReusableErrorCode = Extract<
  | 'auth_not_configured'
  | 'scope_insufficient'
  | 'auth_required'
  | 'bad_request'
  | 'timeout'
  | 'network',
  string
>;

/** The domain-only outcomes + the closed fallback (no silent fall-through). */
export type ToolUsageDomainErrorCode =
  | 'unknown_tool'
  | 'tool_usage_unavailable'
  | 'unexpected';

/** Every outcome a failed tool-usage read can decode to. */
export type ToolUsageErrorCode = ToolUsageReusableErrorCode | ToolUsageDomainErrorCode;

/**
 * The domain panel's banner — a DISCRIMINATED shape, not a bare code
 * (the shared `app-metrics-door-state` accepts a DOOR class only).
 */
export type ToolUsageBanner =
  | { readonly kind: 'door'; readonly code: ToolUsageReusableErrorCode }
  | { readonly kind: 'domain'; readonly code: ToolUsageDomainErrorCode };

/** The structured copy of a DOMAIN-ONLY outcome (the door-state shape). */
export interface ToolUsageDomainCopy {
  readonly cause: string;
  readonly meaning: string;
  readonly action: string;
  readonly pointer: string | null;
  readonly retryable: boolean;
}

/**
 * The DOMAIN-LOCAL copy table — exhaustive over the domain union, no
 * silent fall-through (asserted exhaustively by
 * `tool-usage.models.spec.ts`: every domain class has exactly one row).
 */
export const TOOL_USAGE_DOMAIN_COPY: Readonly<
  Record<ToolUsageDomainErrorCode, ToolUsageDomainCopy>
> = {
  unknown_tool: {
    cause: 'This tool is no longer known (it may have been pruned).',
    meaning: 'The ledger has no rows for it, so no daily series exists.',
    action: 'Pick another tool in the table.',
    pointer: null,
    retryable: false,
  },
  tool_usage_unavailable: {
    cause: 'The tool-usage store is unavailable on this daemon.',
    meaning: 'The route answered a service failure — no series was read.',
    action: 'Retry the read; a store that stays unavailable needs an operator.',
    pointer: null,
    retryable: true,
  },
  unexpected: {
    cause: 'The tool-usage route returned an unexpected error.',
    meaning: 'The failure matches no class the route documents.',
    action: 'Retry the read; a repeated failure needs the response body.',
    pointer: null,
    retryable: true,
  },
};

/**
 * Decode a failed tool-usage read into its outcome class.
 *
 * The mapping is the plan's Task 2 door table, verbatim:
 *
 *   | the route's answer          | the FE renders                    |
 *   |-----------------------------|-----------------------------------|
 *   | 503 AUTH_NOT_CONFIGURED     | `auth_not_configured` (door)      |
 *   | 403 SCOPE_INSUFFICIENT      | `scope_insufficient` (door)       |
 *   | 401                         | `auth_required` (door)            |
 *   | 400                         | `bad_request` (door)              |
 *   | client deadline             | `timeout` (door)                  |
 *   | transport (status 0)        | `network` (door)                  |
 *   | 404 unknown_tool            | `unknown_tool` (domain)           |
 *   | 503 tool_usage_unavailable  | `tool_usage_unavailable` (domain) |
 *   | anything else               | `unexpected` (domain)             |
 *
 * The `unexpected` fallback is DOMAIN-LOCAL on purpose (the nest-reports
 * precedent): the door's `unexpected` copy says "the metrics door
 * returned…", a false subject for this route.
 *
 * The auth/scope/`tool_usage_unavailable` tokens are matched
 * CASE-INSENSITIVELY where the shared decoder does (the shipped token
 * is UPPERCASE; a proxy may lower-case it).
 */
export function decodeToolUsageError(err: unknown): ToolUsageErrorCode {
  const status = statusOf(err);
  const rawCode = rawErrorCode(err);
  // The 404 and the 503 branches are the DOMAIN outcomes — the door's
  // own classes would state the wrong thing for them.
  if (status === 404) {
    return 'unknown_tool';
  }
  if (status === 503) {
    if (rawCode !== null && rawCode.toUpperCase() === 'AUTH_NOT_CONFIGURED') {
      return 'auth_not_configured';
    }
    if (rawCode !== null && rawCode.toLowerCase() === 'tool_usage_unavailable') {
      return 'tool_usage_unavailable';
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
