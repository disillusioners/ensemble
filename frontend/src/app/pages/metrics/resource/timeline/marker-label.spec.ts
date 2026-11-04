// The VISIBLE MARKER LABEL spec (T1 phase 5, task 5.7 — ADJ-iv shape 1).
//
// The load-bearing pin: the label carries the FOUR API words the
// endpoint already serves — `kind`, `severity`, `series_key`, `peak` —
// so the marker says them visibly (never hover-only again).

import { Anomaly } from '../../../../models/resources.models';
import { anomalyMarkerLabel } from './marker-label';

function makeAnomaly(overrides: Partial<Anomaly> = {}): Anomaly {
  return {
    id: 'anom-000003',
    kind: 'cpu',
    t_start: '2026-11-03T09:30:00',
    t_end: '2026-11-03T09:40:00',
    peak: 75.55,
    baseline: 12,
    severity: 'medium',
    series_key: 'daemon_cpu',
    shape: 'spike',
    ...overrides,
  };
}

describe('anomalyMarkerLabel — the four visible API words (5.7)', () => {
  it('carries kind, severity, series_key and peak — all four fields', () => {
    const label = anomalyMarkerLabel(makeAnomaly());
    expect(label).toContain('cpu'); // kind
    expect(label).toContain('medium'); // severity
    expect(label).toContain('daemon_cpu'); // series_key
    expect(label).toContain('75.55'); // peak
    expect(label).toContain('severity medium');
    expect(label).toContain('series daemon_cpu');
    expect(label).toContain('peak 75.55');
  });

  it('is composed FROM the wire anomaly — a different anomaly composes a different label', () => {
    const a = anomalyMarkerLabel(makeAnomaly());
    const b = anomalyMarkerLabel(makeAnomaly({ kind: 'memory', severity: 'high', series_key: 'daemon_rss_mb', peak: 1900 }));
    expect(a).not.toBe(b);
    expect(b).toContain('memory');
    expect(b).toContain('high');
    expect(b).toContain('daemon_rss_mb');
    expect(b).toContain('1900');
  });

  it('keeps the shape word (the drill-down summary stays the hover parity)', () => {
    expect(anomalyMarkerLabel(makeAnomaly())).toContain('spike');
  });
});
