// The DRILL-DOWN POLICY's spec (T1 phase 4, task 4.2).
//
// The padded drill-down window moved OUT of the chart engine (it is
// policy, not geometry) — this is its new home, and this is where its
// assertion lives (the disposition table's T5 "re-point — MOVED").

import { Anomaly } from '../../../../models/resources.models';
import { anomalyDrilldownWindow } from './timeline-drilldown';

function anomaly(overrides: Partial<Anomaly> = {}): Anomaly {
  return {
    id: 'anom-1',
    kind: 'memory',
    t_start: '2026-10-18T16:30:00',
    t_end: '2026-10-18T16:40:00',
    peak: 1900,
    baseline: 1700,
    severity: 'high',
    series_key: 'daemon_rss_mb',
    shape: 'ramp',
    ...overrides,
  };
}

describe('the drill-down policy (the ported anomalyDrilldownWindow)', () => {
  it('pads by max(60s, 10% of the anomaly span)', () => {
    // 10-minute span → 10% = 60s, so pad = 60s.
    expect(anomalyDrilldownWindow(anomaly())).toEqual({
      t0: '2026-10-18T16:29:00.000Z',
      t1: '2026-10-18T16:41:00.000Z',
    });

    // 30-minute span → 10% = 180s beats the 60s floor.
    expect(
      anomalyDrilldownWindow(
        anomaly({ t_start: '2026-10-18T16:00:00', t_end: '2026-10-18T16:30:00' }),
      ),
    ).toEqual({
      t0: '2026-10-18T15:57:00.000Z',
      t1: '2026-10-18T16:33:00.000Z',
    });
  });
});
