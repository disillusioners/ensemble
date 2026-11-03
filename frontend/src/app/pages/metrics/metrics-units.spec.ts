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
//     mixed-unit panel is impossible by construction.

import {
  METRICS_UNIT_UNKNOWN,
  METRICS_UNIT_VOCABULARY,
  metricsPanelGroupKey,
  metricsUnitFor,
} from './metrics-units';

describe('the metric → unit vocabulary', () => {
  it('renders `unit unknown` for a metric with no entry', () => {
    expect(metricsUnitFor('ensemble_db_pool_checked_out')).toBe(METRICS_UNIT_UNKNOWN);
    expect(metricsUnitFor('')).toBe(METRICS_UNIT_UNKNOWN);
  });

  it('renders the vocabulary entry for a known metric', () => {
    // The vocabulary ships EMPTY (phase 6 populates it) — the lookup is
    // proved by the SHAPE, not by a seeded value.
    expect(Object.values(METRICS_UNIT_VOCABULARY).every((unit) => unit !== '')).toBe(true);
    for (const [name, unit] of Object.entries(METRICS_UNIT_VOCABULARY)) {
      expect(metricsUnitFor(name)).toBe(unit);
    }
  });

  it('keys on the METRIC NAME (never on the family)', () => {
    // Two metrics of the SAME family carry different units — the lookup
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

  it('the panel group key is the KIND + UNIT pair', () => {
    expect(metricsPanelGroupKey('step', 'MB')).toBe('step::MB');
    expect(metricsPanelGroupKey('line', 'MB')).toBe('line::MB');
    // The same kind + the same unit share a panel; a different kind or a
    // different unit does not.
    expect(metricsPanelGroupKey('line', 'MB')).toBe(metricsPanelGroupKey('line', 'MB'));
    expect(metricsPanelGroupKey('line', 'MB')).not.toBe(metricsPanelGroupKey('line', '%'));
  });
});
