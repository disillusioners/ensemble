// The METRIC → UNIT VOCABULARY's spec (T1 phase 4, task 4.15 — the
// mechanism; phase 6, task 6.3 — the CONTENT).
//
// The load-bearing pins:
//
//   * a metric with NO vocabulary entry renders `unit unknown` — never a
//     guess, never a borrowed unit (IMPL-10 (iii): a wrong unit is worse
//     than no unit);
//   * the unit keys on the METRIC NAME (never on the family);
//   * the panel group key is the KIND + UNIT pair — a mixed-kind or a
//     mixed-unit panel is impossible by construction;
//   * PHASE 6: the vocabulary is POPULATED for the series the lane
//     renders — every entry non-empty, the mixed-kind families carry
//     their two honest units, and the unit is the DERIVED one (a wrong
//     entry fails the spec — the mutation-proven leg).

import { MetricsChartRole } from '../../models/metrics.model';
import {
  METRICS_UNIT_UNKNOWN,
  METRICS_UNIT_VOCABULARY,
  metricsPanelGroupKey,
  metricsUnitFor,
} from './metrics-units';

describe('the metric → unit vocabulary', () => {
  it('renders `unit unknown` for a metric with no entry', () => {
    // A name OUTSIDE the vocabulary — a metric the lane does not render
    // (F2 is adjudicated OUT; F14/F15 registered nothing). The fallback
    // is the contract: no guess, no borrowed sibling unit.
    expect(metricsUnitFor('ensemble_unknown_metric')).toBe(METRICS_UNIT_UNKNOWN);
    expect(metricsUnitFor('')).toBe(METRICS_UNIT_UNKNOWN);
    // Even a name SHAPE that looks like it carries a unit gets no
    // inference — the table is the only source (IMPL-10 (iii)).
    expect(metricsUnitFor('ensemble_something_mb')).toBe(METRICS_UNIT_UNKNOWN);
  });

  it('renders the vocabulary entry for every known metric', () => {
    // PHASE 6: the vocabulary is populated — every entry is a non-empty
    // unit string and the lookup returns exactly it.
    expect(Object.keys(METRICS_UNIT_VOCABULARY).length).toBeGreaterThan(0);
    for (const [name, unit] of Object.entries(METRICS_UNIT_VOCABULARY)) {
      expect(unit).not.toBe('');
      expect(metricsUnitFor(name)).toBe(unit);
    }
  });

  it('keys on the METRIC NAME (never on the family)', () => {
    // Two metrics of the SAME family carry their own units — the lookup
    // is per name, so the two never collide and neither borrows the
    // other's unit.
    const vocabulary = {
      ensemble_db_pool_checked_out: 'count',
      ensemble_db_pool_requests_errors_total: 'count',
      ensemble_loop_lag_seconds: 's',
    };
    expect(metricsUnitFor('ensemble_db_pool_checked_out', vocabulary)).toBe('count');
    expect(metricsUnitFor('ensemble_loop_lag_seconds', vocabulary)).toBe('s');
    // A name outside the vocabulary never borrows a sibling's unit.
    expect(metricsUnitFor('ensemble_unknown_metric', vocabulary)).toBe(METRICS_UNIT_UNKNOWN);
  });

  it('the F4 family (a gauge AND a counter) carries honest units per metric', () => {
    // w1.py:194-228 — F4 registers `ensemble_db_pool_native` (gauge) AND
    // `ensemble_db_pool_requests_errors_total` (counter). A family-keyed
    // unit would be false for one of them; the per-name table is how
    // both stay honest.
    expect(metricsUnitFor('ensemble_db_pool_native')).toBe('count');
    expect(metricsUnitFor('ensemble_db_pool_requests_errors_total')).toBe('count');
  });

  it('the F9 family yields TWO DIFFERENT units — the counter and the 0/1 gauge', () => {
    // w2.py:120-150 — F9 registers a counter AND a 0/1 gauge. The two
    // panel group keys must differ (this is the property that keeps the
    // family on two panels with different unit axes).
    const counterUnit = metricsUnitFor('ensemble_readiness_probe_total');
    const flagUnit = metricsUnitFor('ensemble_readiness_grace_active');
    expect(counterUnit).toBe('count');
    expect(flagUnit).toBe('flag');
    expect(metricsPanelGroupKey('step', counterUnit)).not.toBe(
      metricsPanelGroupKey('line', flagUnit),
    );
  });

  it('the resource timeline series carry the units the resources API names', () => {
    expect(metricsUnitFor('daemon_rss_mb')).toBe('MB');
    expect(metricsUnitFor('cgroup_mem_mb')).toBe('MB');
    expect(metricsUnitFor('daemon_cpu')).toBe('%');
    expect(metricsUnitFor('threads')).toBe('count');
    expect(metricsUnitFor('instances_active')).toBe('count');
  });

  it('no entry claims a unit the daemon does not state', () => {
    // The honest-unit census: the table's unit strings come from the
    // closed set the wiring files state — seconds, counts, bytes, the
    // resources API's MB/%, the 0/1 flags, the state code and the share.
    const allowed = new Set(['s', 'count', 'bytes', 'MB', '%', 'flag', 'state', 'ratio']);
    for (const unit of Object.values(METRICS_UNIT_VOCABULARY)) {
      expect(allowed.has(unit)).toBe(true);
    }
  });
});

describe('the vocabulary × the wire chart_role (phase 6, IMPL-10)', () => {
  // The ROLE comes from the wire (`chart_role`, metrics.model.ts:164) —
  // never from an FE table. These legs pin the division of labour: the
  // vocabulary owns the UNIT ONLY; the role is whatever the wire says,
  // even when it disagrees with an FE-shaped guess.
  const wireRoles: readonly [string, MetricsChartRole][] = [
    ['ensemble_db_pool_checked_out', 'line'],
    ['ensemble_db_pool_requests_errors_total', 'step'],
    ['ensemble_readiness_probe_total', 'step'],
    ['ensemble_readiness_grace_active', 'line'],
  ];

  it('the vocabulary never derives the kind — the role is free to be anything', () => {
    // A gauge-role name and a counter-role name can carry the SAME unit
    // (F4's two count series) — the unit table must not have opinions
    // about the kind, and the group key must still separate them.
    for (const [name] of wireRoles) {
      expect(METRICS_UNIT_VOCABULARY[name]).toBeDefined();
    }
    // Same unit, different roles ⇒ DIFFERENT group keys (the F4 shape).
    expect(metricsPanelGroupKey('line', 'count')).not.toBe(metricsPanelGroupKey('step', 'count'));
  });
});
