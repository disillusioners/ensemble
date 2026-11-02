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

import { readFileSync } from 'fs';
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
    for (const shape of [
      { status: 401, error: { detail: { error: 'AUTH_REQUIRED', message: 'auth required' } } },
      { status: 404, error: { detail: { error: 'unknown_family', message: 'no such family' } } },
      { status: 400, error: { detail: { error: 'invalid_cursor', message: 'bad cursor' } } },
      { status: 500 },
    ]) {
      const decoded = decodeMetricsDoorError(shape);
      expect(decoded.code).toBe('unexpected');
      expect(decoded.copy).toBe(METRICS_DOOR_ERROR_COPY.unexpected);
      expect(decoded.copy).not.toBe(METRICS_DOOR_ERROR_COPY.network);
    }
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
});
