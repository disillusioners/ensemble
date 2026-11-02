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
  MetricsFamily,
  MetricsFamilyName,
  MetricsSnapshotPayload,
  MetricsSeriesPayload,
  formatAvailabilityReason,
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
    };
    expect(payload.series[0].value).toBeNull();
    expect(payload.vocabulary_version).toBe(2);
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
    };
    expect(payload.series[0].points[1][1]).toBe(9);
    expect(payload.window.interval_s_source).toBe('observed');
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

  it('keeps the chart role union to line | step', () => {
    const role: MetricsChartRole = 'line';
    expect(role).toBe('line');
    expect(modelSource).toMatch(/MetricsChartRole = 'line' \| 'step'/);
  });
});
