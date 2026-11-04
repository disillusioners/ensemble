// The metrics-query MODEL MIRROR spec (P5 task 5.1 · D1 pack `metrics-fe-models`).
//
// Two jobs, both load-bearing:
//
// 1. THE FIELD-SET PIN (P5-M5). The mirror must match the WIRE contract
//    field-for-field. The expectation is NOT a hand-written copy of the
//    field list: it is PARSED OUT OF THE FROZEN CONTRACT DOC
//    (`docs/architecture/metrics-query-contract.md` §4.3 — the canonical
//    family shape), so a rename in the mirror turns this spec RED and
//    a rename in the contract turns it RED too (the two sides are
//    compared, never one of them asserted against itself).
//
// 2. THE WIDENED AVAILABILITY UNION (P5-M9 · AM-7). The assignment below
//    only compiles while the union is WIDENED; narrowing it to the
//    closed literal set breaks the FE type-check leg
//    (`tsc --noEmit -p tsconfig.metrics-fe.json`).

import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';

import {
  METRICS_EMPTY_VALUE,
  MetricsAvailabilityReason,
  MetricsChartRole,
  MetricsDeclaredLabels,
  MetricsFamily,
  MetricsFamilyName,
  MetricsLabels,
  MetricsSnapshotPayload,
  MetricsSeriesPayload,
  METRICS_DOOR_ERROR_COPY,
  METRICS_DOOR_STATE_COPY,
  METRICS_NO_LAST_GOOD_SUFFIX,
  MetricsDoorErrorCode,
  MetricsDoorTimeoutError,
  decodeMetricsDoorError,
  formatAvailabilityReason,
  formatMetricsDeclaredLabels,
  formatMetricsLabels,
  formatMetricsValue,
  metricsChartRoleForKind,
} from './metrics.model';

const repoRoot = join(__dirname, '..', '..', '..', '..');
const contractPath = join(repoRoot, 'docs', 'architecture', 'metrics-query-contract.md');
const modelSource = readFileSync(join(__dirname, 'metrics.model.ts'), 'utf-8');

/** Split a brace/bracket body on its TOP-LEVEL commas. */
function splitTopLevel(body: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let current = '';
  for (const ch of body) {
    if (ch === '{' || ch === '[') {
      depth += 1;
    } else if (ch === '}' || ch === ']') {
      depth -= 1;
    }
    if (ch === ',' && depth === 0) {
      out.push(current.trim());
      current = '';
      continue;
    }
    current += ch;
  }
  if (current.trim().length > 0) {
    out.push(current.trim());
  }
  return out;
}

/** The leading field name of a shape member (`foo: bar` / `foo?`). */
function fieldName(entry: string): string {
  return entry.split(':')[0].trim().replace(/\?$/, '');
}

/** The canonical frozen family shape, read from the CONTRACT DOC. */
function canonicalShape(): string {
  const doc = readFileSync(contractPath, 'utf-8');
  const line = doc
    .split('\n')
    .find((candidate) => candidate.trim().startsWith('{id, names:[{name,'));
  expect(line).toBeDefined();
  return (line as string).trim();
}

describe('metrics model mirror — the frozen family shape', () => {
  const shape = canonicalShape();

  it('the contract doc still carries the canonical shape (the pin reads a real anchor)', () => {
    expect(shape.startsWith('{id, names:[{name,')).toBe(true);
    expect(shape.endsWith('}')).toBe(true);
  });

  it('mirrors every TOP-LEVEL field of the canonical shape', () => {
    const body = shape.slice(1, -1);
    const fields = splitTopLevel(body).map(fieldName);
    expect(fields.length).toBeGreaterThan(5);
    for (const field of fields) {
      expect(modelSource).toContain(`readonly ${field}`);
    }
  });

  it('mirrors every PER-NAME field of the canonical shape', () => {
    const body = shape.slice(1, -1);
    const namesEntry = splitTopLevel(body).find((entry) => entry.startsWith('names:'));
    expect(namesEntry).toBeDefined();
    const inner = (namesEntry as string).slice('names:['.length, -1);
    const fields = splitTopLevel(inner.slice(1, -1)).map(fieldName);
    expect(fields).toEqual(
      expect.arrayContaining([
        'name',
        'kind',
        'labels',
        'help',
        'buckets',
        'cardinality_class',
        'monotonic',
      ]),
    );
    for (const field of fields) {
      expect(modelSource).toContain(`readonly ${field}`);
    }
  });

  it('types the per-name labels as the DECLARED (list-valued) form, not the merged one (W-6)', () => {
    // The frozen contract shape is `labels:{key:[values]}|null` — the
    // value type is a LIST per key. The mirror carries a DEDICATED
    // declared type; a re-merged string-valued labels on the family
    // name would present a whole vocabulary as one concrete value
    // (`op=aget,aget_tuple`) — the exact W-6 defect.
    expect(modelSource).toMatch(
      /interface MetricsDeclaredLabels \{\s*readonly \[key: string\]: readonly string\[\];\s*\}/,
    );
    // The declaration is scoped to the MetricsFamilyName INTERFACE BODY
    // (a file-wide regex would match the series interfaces' concrete
    // labels member — a vacuous guard).
    const nameBlock = modelSource.slice(modelSource.indexOf('interface MetricsFamilyName'));
    const nameBody = nameBlock.slice(0, nameBlock.indexOf('}', nameBlock.indexOf('{')) + 1);
    expect(nameBody).toMatch(/readonly labels: MetricsDeclaredLabels \| null;/);
    expect(nameBody).not.toMatch(/readonly labels: MetricsLabels/);
  });

  it('types the SERIES labels as the CONCRETE (string-valued) form (W-6)', () => {
    // /snapshot and /series carry one entry per LIVE label combination
    // — every value a string (the runtime observation §3.2).
    expect(modelSource).toMatch(/interface MetricsLabels \{\s*readonly \[key: string\]: string;\s*\}/);
    expect(modelSource).toMatch(
      /interface MetricsSnapshotSeries[^]*?readonly labels: MetricsLabels;/,
    );
    expect(modelSource).toMatch(/interface MetricsSeriesEntry[^]*?readonly labels: MetricsLabels;/);
  });

  it('renders a DECLARED set as a set, never as a concrete key=value (W-6)', () => {
    const declared: MetricsDeclaredLabels = { op: ['aget_tuple', 'aget'] };
    const rendered = formatMetricsDeclaredLabels(declared);
    expect(rendered).toBe('op∈{aget, aget_tuple}');
    // The W-6 symptom verbatim: the merged rendering that presented a
    // whole vocabulary as one observed value.
    expect(rendered).not.toBe('op=aget_tuple,aget');
    expect(rendered).not.toContain('op=aget');
    expect(formatMetricsDeclaredLabels(null)).toBe('');
    expect(formatMetricsDeclaredLabels({})).toBe('');
  });

  it('keeps the CONCRETE formatter string-valued (the two formatters never merge)', () => {
    const concrete: MetricsLabels = { pool: 'native', quantity: 'checked_out' };
    expect(formatMetricsLabels(concrete)).toBe('pool=native quantity=checked_out');
    expect(formatMetricsLabels(null)).toBe('');
    expect(formatMetricsLabels({})).toBe('');
  });

  it('carries the ADDITIVE per-name chart_role (contract §4.3, AM-7 — no schema bump)', () => {
    expect(modelSource).toContain('readonly chart_role');
  });

  it('types vocabulary_version as a number, never a literal (AM-7)', () => {
    expect(modelSource).toMatch(/readonly vocabulary_version: number;/);
    expect(modelSource).not.toMatch(/vocabulary_version: \d/);
  });

  it('keeps schema_version a separate axis from vocabulary_version', () => {
    expect(modelSource).toMatch(/readonly schema_version: number;/);
  });

  it('declares no `summary` kind (AM-4 — the registry never produces one)', () => {
    expect(modelSource).toMatch(/MetricsMetricKind = 'gauge' \| 'counter' \| 'histogram'/);
    expect(modelSource).not.toContain("'summary'");
  });
});

describe('metrics model mirror — the widened availability union (AM-7 / P5-M9)', () => {
  it('accepts a reason the door has not defined yet (TYPE-LEVEL: this is the pin)', () => {
    // This assignment compiles ONLY while the union is WIDENED. Narrowing
    // `MetricsAvailabilityReason` to the closed literal set breaks the
    // FE type-check leg — that is the P5-M9 RED.
    const futureReason: MetricsAvailabilityReason =
      'a_reason_the_door_has_not_defined_yet';
    expect(futureReason).toBe('a_reason_the_door_has_not_defined_yet');
  });

  it('renders an unrecognised reason as unknown(<value>)', () => {
    expect(formatAvailabilityReason('a_reason_the_door_has_not_defined_yet')).toBe(
      'unknown(a_reason_the_door_has_not_defined_yet)',
    );
  });

  it('renders every KNOWN reason without the unknown() fallback', () => {
    const known = [
      'census_gated',
      'census_not_running',
      'capture_off',
      'series_absent',
      'listener_disabled',
    ];
    for (const reason of known) {
      const rendered = formatAvailabilityReason(reason);
      expect(rendered).not.toContain('unknown(');
      expect(rendered.length).toBeGreaterThan(0);
    }
  });

  it('renders a missing reason as the em-dash placeholder', () => {
    expect(formatAvailabilityReason(null)).toBe(METRICS_EMPTY_VALUE);
    expect(formatAvailabilityReason(undefined)).toBe(METRICS_EMPTY_VALUE);
    expect(formatAvailabilityReason('')).toBe(METRICS_EMPTY_VALUE);
  });
});

describe('metrics model mirror — the value/label helpers', () => {
  it('renders a nullable scalar as the em-dash placeholder', () => {
    expect(formatMetricsValue(null)).toBe('—');
    expect(formatMetricsValue(undefined)).toBe('—');
    expect(formatMetricsValue(Number.NaN)).toBe('—');
    expect(formatMetricsValue(0)).toBe('0');
    expect(formatMetricsValue(12.5)).toBe('12.5');
  });

  it('renders the label set as a sorted key=value string', () => {
    expect(formatMetricsLabels({ pool: 'native', quantity: 'checked_out' })).toBe(
      'pool=native quantity=checked_out',
    );
    expect(formatMetricsLabels(null)).toBe('');
    expect(formatMetricsLabels({})).toBe('');
  });

  it('derives the rendering mode from the kind exactly as the door does', () => {
    expect(metricsChartRoleForKind('counter')).toBe('step');
    expect(metricsChartRoleForKind('gauge')).toBe('line');
    expect(metricsChartRoleForKind('histogram')).toBe('line');
  });
});

describe('metrics model mirror — the payload shapes', () => {
  it('types the snapshot series value as nullable (a histogram carries null)', () => {
    const series = {
      name: 'ensemble_loop_lag_seconds',
      labels: { stat: 'p50' },
      kind: 'gauge' as const,
      monotonic: false,
      value: null,
      last_publish_age_s: 3.5,
    };
    const payload: MetricsSnapshotPayload = {
      schema_version: 1,
      vocabulary_version: 2,
      generated_at_utc: '2026-11-02T09:00:00+00:00',
      restart_epoch: 'abc123',
      activation: {
        listener_enabled: false,
        census_enabled: true,
        query_api: 'auth-gated-always-on',
      },
      series: [series],
      truncated: false,
    };
    expect(payload.series[0].value).toBeNull();
    expect(payload.vocabulary_version).toBe(2);
    // The W-4 additive bound members — the bounded body's cursor is
    // PRESENT-ONLY-WHEN-TRUNCATED on the wire (the door pops it when
    // unbounded), so the mirror types it optional.
    expect(payload.truncated).toBe(false);
    expect(payload.next_cursor).toBeUndefined();
  });

  it('carries the bounded body members on /series and /snapshot (W-2 / W-4)', () => {
    for (const payloadType of ['MetricsSnapshotPayload', 'MetricsSeriesPayload']) {
      const block = modelSource.slice(modelSource.indexOf(`interface ${payloadType}`));
      const body = block.slice(0, block.indexOf('}', block.indexOf('{')) + 1);
      expect(body).toMatch(/readonly truncated: boolean;/);
      expect(body).toMatch(/readonly next_cursor\?: string;/);
    }
    const familiesBlock = modelSource.slice(
      modelSource.indexOf('interface MetricsFamiliesPayload'),
    );
    expect(familiesBlock).toMatch(/readonly truncated: boolean;/);
    expect(familiesBlock).toMatch(/readonly next_cursor\?: string;/);
  });

  it('types the trend points as [t, v] tuples and carries the staleness flags', () => {
    const payload: MetricsSeriesPayload = {
      schema_version: 1,
      vocabulary_version: 2,
      generated_at_utc: '2026-11-02T09:00:00+00:00',
      restart_epoch: 'abc123',
      activation: {
        listener_enabled: false,
        census_enabled: true,
        query_api: 'auth-gated-always-on',
      },
      window: {
        from_utc: '2026-11-02T08:00:00+00:00',
        to_utc: '2026-11-02T09:00:00+00:00',
        interval_s: 15,
        interval_s_source: 'observed',
      },
      last_sample_age_s: 2,
      stale: false,
      series: [
        {
          name: 'ensemble_job_queue_items_created_total',
          labels: {},
          kind: 'counter',
          monotonic: true,
          points: [
            [1_700_000_000, 4],
            [1_700_000_015, 9],
          ],
        },
      ],
      truncated: true,
      next_cursor: 'opaque-token-from-the-door',
    };
    expect(payload.series[0].points[1][1]).toBe(9);
    expect(payload.window.interval_s_source).toBe('observed');
    // A bound hit is never silent: the flag is true and the cursor
    // follows (contract §5, W-2).
    expect(payload.truncated).toBe(true);
    expect(payload.next_cursor).toBe('opaque-token-from-the-door');
  });

  it('types the family shape with the additive per-name chart_role', () => {
    const name: MetricsFamilyName = {
      name: 'ensemble_db_pool_requests_errors_total',
      kind: 'counter',
      labels: null,
      help: '',
      buckets: null,
      cardinality_class: 'open',
      monotonic: true,
      chart_role: 'step',
    };
    const family: MetricsFamily = {
      id: 'F4',
      names: [name],
      live_series_count: 0,
      availability: { state: 'dark', reason: 'series_absent' },
      role: 'chart',
      source_idiom: 'registry',
      durability: 'process',
      cardinality_contribution: 0,
    };
    expect(name.chart_role).toBe('step');
    expect(family.availability.state).toBe('dark');
  });

  it('types a CLOSED family name with the DECLARED list-valued sets (W-6 — the value-type pin)', () => {
    // This fixture compiles ONLY while the per-name labels carry the
    // DECLARED (list-valued) type. Planting the merged string-valued
    // `MetricsLabels` back onto `MetricsFamilyName.labels` breaks THIS
    // assignment — the RED surface is the scoped type-check leg, which
    // is exactly where a value-type drift is visible (jest does not
    // type-check).
    const name: MetricsFamilyName = {
      name: 'ensemble_checkpoint_op_bytes',
      kind: 'histogram',
      labels: { op: ['aget', 'aget_tuple'] },
      help: 'checkpoint op byte sizes',
      buckets: [1024, 65536],
      cardinality_class: 'closed',
      monotonic: false,
      chart_role: 'line',
    };
    expect(name.labels).toEqual({ op: ['aget', 'aget_tuple'] });
    expect(formatMetricsDeclaredLabels(name.labels)).toBe('op∈{aget, aget_tuple}');
  });

  it('keeps the chart role union to line | step', () => {
    const role: MetricsChartRole = 'line';
    expect(role).toBe('line');
    expect(modelSource).toMatch(/MetricsChartRole = 'line' \| 'step'/);
  });
});

describe('metrics model mirror — the door error decode (A-5, contract §5)', () => {
  it('branches a 403 scope miss to the scope copy (never the unreachable claim)', () => {
    const decoded = decodeMetricsDoorError({
      status: 403,
      error: { detail: { error: 'SCOPE_INSUFFICIENT', message: 'Token lacks required scope' } },
    });
    expect(decoded.code).toBe('scope_insufficient');
    expect(decoded.copy).toBe(METRICS_DOOR_ERROR_COPY.scope_insufficient);
    expect(decoded.copy).toContain('scope');
    expect(decoded.copy).not.toBe(METRICS_DOOR_ERROR_COPY.network);
  });

  it('branches a 503 AUTH_NOT_CONFIGURED and a 503 registry_unavailable to distinct service copies', () => {
    const authOff = decodeMetricsDoorError({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED', message: 'Auth not configured (fail-closed)' } },
    });
    expect(authOff.code).toBe('auth_not_configured');
    expect(authOff.copy).toBe(METRICS_DOOR_ERROR_COPY.auth_not_configured);
    expect(authOff.copy).not.toBe(METRICS_DOOR_ERROR_COPY.network);

    const unwired = decodeMetricsDoorError({
      status: 503,
      error: { detail: { error: 'registry_unavailable', message: 'provider not wired' } },
    });
    expect(unwired.code).toBe('registry_unavailable');
    expect(unwired.copy).toBe(METRICS_DOOR_ERROR_COPY.registry_unavailable);
    // The two service failures carry DISTINCT copies — one names the
    // fail-closed auth posture, the other the unwired provider.
    expect(authOff.copy).not.toBe(unwired.copy);
  });

  it('branches a transport failure (status 0) to the unreachable copy', () => {
    const decoded = decodeMetricsDoorError({ status: 0 });
    expect(decoded.code).toBe('network');
    expect(decoded.copy).toBe(METRICS_DOOR_ERROR_COPY.network);
    expect(decoded.copy).toContain('unreachable');
  });

  it('falls back honestly for every other failure — never the unreachable claim', () => {
    // ── RE-POINTED BY PHASE 8 (task 8.1 / AM-4) ──────────────────────
    // This leg used to assert that 401, 404 and 400 all decode to
    // `unexpected`. That WAS the defect: the FIRST state every real
    // operator meets (401) wore the "unexpected error" copy and a live
    // Retry button. The three statuses now carry their OWN classes, so
    // the leg asserts the NEW mapping and keeps its original intent —
    // a status the door does not document never wears the
    // "unreachable" claim.
    const mapped = [
      {
        status: 401,
        error: { detail: { error: 'AUTH_REQUIRED', message: 'auth required' } },
        code: 'auth_required',
      },
      {
        status: 404,
        error: { detail: { error: 'unknown_family', message: 'no such family' } },
        code: 'unknown_family',
      },
      {
        status: 400,
        error: { detail: { error: 'invalid_cursor', message: 'bad cursor' } },
        code: 'bad_request',
      },
    ];
    for (const shape of mapped) {
      const decoded = decodeMetricsDoorError(shape);
      expect(decoded.code).toBe(shape.code);
      expect(decoded.copy).toBe(METRICS_DOOR_ERROR_COPY[shape.code as MetricsDoorErrorCode]);
      expect(decoded.copy).not.toBe(METRICS_DOOR_ERROR_COPY.network);
    }
    // A status the door does NOT document still falls back — to the
    // honest fallback, never to the unreachable claim.
    const undocumented = decodeMetricsDoorError({ status: 500 });
    expect(undocumented.code).toBe('unexpected');
    expect(undocumented.copy).toBe(METRICS_DOOR_ERROR_COPY.unexpected);
    expect(undocumented.copy).not.toBe(METRICS_DOOR_ERROR_COPY.network);
  });

  it('the flat body shape decodes too (the auth-middleware shape)', () => {
    const decoded = decodeMetricsDoorError({ status: 503, error: { error: 'AUTH_NOT_CONFIGURED' } });
    expect(decoded.code).toBe('auth_not_configured');
    const scoped = decodeMetricsDoorError({ status: 403, error: { error: 'SCOPE_INSUFFICIENT' } });
    expect(scoped.code).toBe('scope_insufficient');
  });

  it('the three branches carry THREE DISTINCT copies (the A-5 pin)', () => {
    const copies = [
      METRICS_DOOR_ERROR_COPY.scope_insufficient,
      METRICS_DOOR_ERROR_COPY.auth_not_configured,
      METRICS_DOOR_ERROR_COPY.network,
    ];
    expect(new Set(copies).size).toBe(3);
    // The pre-fix defect: the scope and the service failures wore the
    // unreachable copy. The NETWORK copy is the one copy that may say
    // it — every other branch must differ from it.
    expect(METRICS_DOOR_ERROR_COPY.scope_insufficient).not.toBe(
      METRICS_DOOR_ERROR_COPY.network,
    );
    expect(METRICS_DOOR_ERROR_COPY.auth_not_configured).not.toBe(
      METRICS_DOOR_ERROR_COPY.network,
    );
    expect(METRICS_DOOR_ERROR_COPY.registry_unavailable).not.toBe(
      METRICS_DOOR_ERROR_COPY.network,
    );
    expect(METRICS_DOOR_ERROR_COPY.unexpected).not.toBe(METRICS_DOOR_ERROR_COPY.network);
  });

  it('decodes the CLIENT-SIDE deadline to the `timeout` class (T1 phase 2, 2.10)', () => {
    const decoded = decodeMetricsDoorError(new MetricsDoorTimeoutError());
    expect(decoded.code).toBe('timeout');
    expect(decoded.copy).toBe(METRICS_DOOR_ERROR_COPY.timeout);
    // The timeout copy must NOT claim the door is unreachable — the
    // client gave up, the door may be perfectly reachable.
    expect(decoded.copy).not.toBe(METRICS_DOOR_ERROR_COPY.network);
    expect(decoded.copy.toLowerCase()).not.toContain('unreachable');
  });

  it('the timeout copy is DISTINCT from every other class copy', () => {
    for (const [name, copy] of Object.entries(METRICS_DOOR_ERROR_COPY)) {
      if (name === 'timeout') {
        continue;
      }
      expect(METRICS_DOOR_ERROR_COPY.timeout).not.toBe(copy);
    }
  });
});

// ── THE PHASE-8 DOOR-STATE TABLE (task 8.1 / 8.11 / 8.12) ────────────────
//
// The table is a CLOSED UNION of TEN classes with a per-class copy,
// action and RETRY POLICY. Three pins carry the weight:
//
//   1. EXHAUSTIVE — every class in the union has a copy entry, and
//      every copy entry names a class. A class added to the union
//      without a copy breaks the build (the table is the SSOT).
//   2. THE 403 BRANCHES — `SCOPE_INSUFFICIENT` ⇒ `scope_insufficient`;
//      ANY other 403 ⇒ `forbidden_other`. The OLD predicate labelled
//      every 403-with-a-code as a scope failure — a false statement
//      about the operator's session (8.12 / AM-20).
//   3. THE COPY SSOT — the flat banner map is DERIVED from the
//      structured one, and the two duplicated suffixes that lived in
//      two tab bodies now live HERE (8.11 / AM-20).

/** The TEN classes of the closed union — the table's own census. */
const TEN_CLASSES = [
  'auth_required',
  'auth_not_configured',
  'scope_insufficient',
  'forbidden_other',
  'unknown_family',
  'bad_request',
  'timeout',
  'registry_unavailable',
  'network',
  'unexpected',
] as const;

/** The classes the plan names as NOT retryable (8.1). */
const NO_RETRY_CLASSES = [
  'auth_required',
  'auth_not_configured',
  'unknown_family',
  'bad_request',
  'timeout',
] as const;

describe('metrics model — the CLOSED door-state table (T1 phase 8, task 8.1 / AM-4)', () => {
  it('is a CLOSED union of exactly the TEN classes', () => {
    expect(Object.keys(METRICS_DOOR_STATE_COPY).sort()).toEqual([...TEN_CLASSES].sort());
    expect(Object.keys(METRICS_DOOR_ERROR_COPY).sort()).toEqual([...TEN_CLASSES].sort());
    expect(TEN_CLASSES).toHaveLength(10);
  });

  it('gives EVERY class a cause, a meaning, an action and a pointer slot', () => {
    for (const code of TEN_CLASSES) {
      const copy = METRICS_DOOR_STATE_COPY[code];
      expect(copy).toBeDefined();
      expect(copy.cause.length).toBeGreaterThan(0);
      expect(copy.meaning.length).toBeGreaterThan(0);
      expect(copy.action.length).toBeGreaterThan(0);
      // The pointer slot is ALWAYS present (null when there is no
      // procedure to point at) — never an absent member.
      expect('pointer' in copy).toBe(true);
    }
  });

  it('carries a per-class RETRY POLICY, and the plan\'s five classes are NO-Retry', () => {
    for (const code of TEN_CLASSES) {
      expect(typeof METRICS_DOOR_STATE_COPY[code].retryable).toBe('boolean');
    }
    for (const code of NO_RETRY_CLASSES) {
      expect(METRICS_DOOR_STATE_COPY[code].retryable).toBe(false);
    }
    // The actionable state is a CONFIGURATION error — a Retry on it can
    // never succeed.
    expect(METRICS_DOOR_STATE_COPY.auth_not_configured.retryable).toBe(false);
    expect(METRICS_DOOR_STATE_COPY.auth_required.retryable).toBe(false);
  });

  it('the auth_not_configured copy is ACTIONABLE — cause, meaning, action and the RUNBOOK', () => {
    const copy = METRICS_DOOR_STATE_COPY.auth_not_configured;
    expect(copy.cause).toContain('fail-closed');
    expect(copy.meaning).toContain('daemon configuration');
    expect(copy.pointer).toBe('docs/runbooks/oauth2-auth.md');
    expect(copy.action.length).toBeGreaterThan(0);
  });

  it('NO environment-variable name appears in ANY door-state copy (the plan\'s risk row)', () => {
    for (const code of TEN_CLASSES) {
      const copy = METRICS_DOOR_STATE_COPY[code];
      const blob = `${copy.cause} ${copy.meaning} ${copy.action} ${copy.pointer ?? ''}`;
      expect(blob).not.toContain('ENSEMBLE_');
      expect(blob).not.toContain('POSTGRES_');
      expect(blob).not.toContain('OPENAI_');
    }
  });

  it('decodes a 401 to the FIRST-CLASS sign-in state (never `unexpected`)', () => {
    const decoded = decodeMetricsDoorError({
      status: 401,
      error: { detail: { error: 'AUTH_REQUIRED', message: 'authentication required' } },
    });
    expect(decoded.code).toBe('auth_required');
    expect(decoded.copy).toBe(METRICS_DOOR_ERROR_COPY.auth_required);
  });

  it('decodes a 404 to `unknown_family` and a 400 to `bad_request`', () => {
    expect(
      decodeMetricsDoorError({ status: 404, error: { detail: { error: 'unknown_family' } } }).code,
    ).toBe('unknown_family');
    expect(
      decodeMetricsDoorError({ status: 400, error: { detail: { error: 'invalid_cursor' } } }).code,
    ).toBe('bad_request');
    expect(
      decodeMetricsDoorError({ status: 400, error: { detail: { error: 'invalid_label_filter' } } })
        .code,
    ).toBe('bad_request');
  });

  it('THE 403 BRANCHES ARE BOTH PINNED (task 8.12 / AM-20)', () => {
    // Branch A — the scope token ⇒ the scope class.
    const scoped = decodeMetricsDoorError({
      status: 403,
      error: { detail: { error: 'SCOPE_INSUFFICIENT', message: 'Token lacks required scope' } },
    });
    expect(scoped.code).toBe('scope_insufficient');
    // Branch B — ANY other 403 ⇒ the NEW class, NEVER the scope one.
    const other = decodeMetricsDoorError({
      status: 403,
      error: { detail: { error: 'CSRF_FAILED', message: 'csrf guard' } },
    });
    expect(other.code).toBe('forbidden_other');
    expect(other.code).not.toBe('scope_insufficient');
    expect(other.copy).toBe(METRICS_DOOR_ERROR_COPY.forbidden_other);
    // A 403 with NO body code at all is also `forbidden_other`.
    expect(decodeMetricsDoorError({ status: 403 }).code).toBe('forbidden_other');
  });

  it('the scope token is matched CASE-INSENSITIVELY (and only that token)', () => {
    expect(
      decodeMetricsDoorError({ status: 403, error: { detail: { error: 'scope_insufficient' } } })
        .code,
    ).toBe('scope_insufficient');
    expect(
      decodeMetricsDoorError({ status: 403, error: { detail: { error: 'SCOPE_OTHER' } } }).code,
    ).toBe('forbidden_other');
  });

  it('the 503 branches stay distinct — the fail-closed auth and the unwired provider', () => {
    expect(
      decodeMetricsDoorError({ status: 503, error: { detail: { error: 'AUTH_NOT_CONFIGURED' } } })
        .code,
    ).toBe('auth_not_configured');
    expect(
      decodeMetricsDoorError({ status: 503, error: { detail: { error: 'registry_unavailable' } } })
        .code,
    ).toBe('registry_unavailable');
  });
});

describe('metrics model — the COPY SSOT (T1 phase 8, task 8.11 / IMPL-18 / AM-20)', () => {
  it('the flat banner map is DERIVED from the structured one (never a second source)', () => {
    for (const code of TEN_CLASSES) {
      expect(METRICS_DOOR_ERROR_COPY[code]).toBe(METRICS_DOOR_STATE_COPY[code].cause);
    }
  });

  it('the flat map carries NO literal copy of its own (the source has one home)', () => {
    // The flat map is a PROJECTION — the sentences live in the
    // structured table. A second literal table would be the defect the
    // audit found.
    const flatStart = modelSource.indexOf('export const METRICS_DOOR_ERROR_COPY');
    const flatEnd = modelSource.indexOf('};', flatStart);
    const flatBlock = modelSource.slice(flatStart, flatEnd);
    expect(flatBlock).not.toMatch(/scope_insufficient:\s*'/);
    expect(flatBlock).not.toMatch(/auth_not_configured:\s*'/);
    expect(flatBlock).not.toMatch(/network:\s*'/);
  });

  it('the two DUPLICATED suffixes live in the SSOT and nowhere else', () => {
    // The suffixes used to be inlined in the overview and charts tab
    // bodies — the same sentence in two files (8.11).
    const repoFrontend = join(repoRoot, 'frontend', 'src', 'app');
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        if (!entry.name.endsWith('.ts') || entry.name.endsWith('.spec.ts')) {
          continue;
        }
        const text = readFileSync(full, 'utf-8');
        for (const phrase of ['the last known values are kept', 'the last known trend is kept']) {
          if (text.includes(phrase)) {
            offenders.push(`${full}: ${phrase}`);
          }
        }
      }
    };
    walk(repoFrontend);
    // Exactly ONE home: the model's SSOT. The SSOT itself is under
    // `models/`, so the count is 1 per phrase.
    expect(offenders.filter((line) => line.includes('the last known values are kept'))).toHaveLength(
      1,
    );
    expect(offenders.filter((line) => line.includes('the last known trend is kept'))).toHaveLength(
      1,
    );
    expect(offenders.every((line) => line.includes(`${join('models', 'metrics.model.ts')}`))).toBe(
      true,
    );
  });

  it('the SSOT also carries the NO-LAST-GOOD suffix (8.4 — the truthful case)', () => {
    expect(METRICS_NO_LAST_GOOD_SUFFIX.length).toBeGreaterThan(0);
    expect(METRICS_NO_LAST_GOOD_SUFFIX).not.toContain('are kept');
  });
});
