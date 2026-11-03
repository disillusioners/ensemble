// The RANGE + BASELINE DOMAIN spec (T1 phase 3, tasks 3.1 – 3.12).
//
// The load-bearing pins:
//
//   * the OPTION SET is the UNION of the manifest's per-tab ladders, in
//     ascending order, DERIVED — the frame never owns a second list;
//   * the VISIBLE ENFORCEMENT — an option the active tab cannot serve is
//     disabled, never silently clamped by the door;
//   * the KEEP-OR-SNAP rule — a tab switch keeps the window when the new
//     tab serves it and snaps to that tab's default when it does not;
//   * the REQUEST BUILDER — 2W for the baseline, never above the substrate
//     ceiling;
//   * the SERVER-ANCHORED SPLIT — the midpoint comes from the RESPONSE's
//     own window anchors, so a skewed client splits at the SAME instant;
//   * the CLAMPED-PAYLOAD refusal — a payload shorter than 2W yields NO
//     split (the door clamped it silently and answered 200);
//   * the DERIVED interval — the meta line's spacing comes from the
//     payload, not from the door's observed cadence.

import {
  METRICS_BASELINE_CLAMPED_REASON,
  METRICS_BASELINE_CUSTOM_SLOT_REASON,
  METRICS_BASELINE_FIDELITY_DISCLOSURE,
  METRICS_BASELINE_MAX_WINDOW_S,
  METRICS_BASELINE_RANGE_REASON,
  METRICS_RANGE_CUSTOM_SLOT_NOTE,
  METRICS_RANGE_OPTIONS,
  METRICS_WINDOW_CEILING_S,
  MetricsRangeOption,
  metricsCentralWindowSeconds,
  metricsDerivedIntervalS,
  metricsPayloadPointCount,
  metricsRangeEnabledFor,
  metricsResourcesWindow,
  metricsSeriesWindowS,
  metricsSnapNote,
  resolveWindowOnTabChange,
  splitSeriesAtMidpoint,
} from './metrics-range';
import {
  METRICS_CENTRAL_ENTRIES,
  METRICS_CUSTOM_ENTRIES,
  MetricsTabEntry,
  metricsCentralTab,
  metricsCustomTab,
} from './tabs.manifest';
import { MetricsSeriesPayload } from '../../models/metrics.model';

function payload(overrides: Partial<MetricsSeriesPayload> = {}): MetricsSeriesPayload {
  return {
    schema_version: 1,
    vocabulary_version: 3,
    generated_at_utc: '2026-11-03T00:00:00+00:00',
    restart_epoch: 'abc',
    activation: {
      listener_enabled: true,
      census_enabled: true,
      query_api: 'auth-gated-always-on',
    },
    window: {
      from_utc: '2026-11-03T00:00:00+00:00',
      to_utc: '2026-11-03T02:00:00+00:00',
      interval_s: 15,
      interval_s_source: 'observed',
    },
    last_sample_age_s: 1,
    stale: false,
    series: [],
    truncated: false,
    ...overrides,
  };
}

/** A series of evenly spaced points over the payload's window. */
function series(
  name: string,
  points: number,
  spacingSeconds = 30,
): MetricsSeriesPayload['series'][number] {
  const out: [number, number][] = [];
  for (let index = 0; index < points; index += 1) {
    out.push([Math.floor(Date.parse('2026-11-03T00:00:00+00:00') / 1000) + index * spacingSeconds, index]);
  }
  return { name, labels: {}, kind: 'gauge', monotonic: false, points: out };
}

describe('the range domain — the OPTION SET (3.1)', () => {
  it('is the UNION of the manifest\'s per-tab ladders, ascending', () => {
    expect(METRICS_RANGE_OPTIONS).toEqual(['15m', '30m', '1h', '2h', '24h', '7d']);
  });

  it('is DERIVED from the manifest — every option is on some tab', () => {
    const served = new Set<MetricsRangeOption>();
    for (const tab of [...METRICS_CENTRAL_ENTRIES, ...METRICS_CUSTOM_ENTRIES]) {
      for (const option of tab.allowedWindows) {
        served.add(option);
      }
    }
    expect(new Set(METRICS_RANGE_OPTIONS)).toEqual(served);
  });

  it('a CENTRAL tab cannot serve the resources-only options', () => {
    const central = metricsCentralTab('runtime');
    expect(metricsRangeEnabledFor('15m', central)).toBe(true);
    expect(metricsRangeEnabledFor('30m', central)).toBe(true);
    expect(metricsRangeEnabledFor('1h', central)).toBe(true);
    expect(metricsRangeEnabledFor('2h', central)).toBe(true);
    expect(metricsRangeEnabledFor('24h', central)).toBe(false);
    expect(metricsRangeEnabledFor('7d', central)).toBe(false);
  });

  it('the CUSTOM tab cannot serve the central-only options', () => {
    const custom = metricsCustomTab();
    expect(metricsRangeEnabledFor('15m', custom)).toBe(true);
    expect(metricsRangeEnabledFor('1h', custom)).toBe(true);
    expect(metricsRangeEnabledFor('24h', custom)).toBe(true);
    expect(metricsRangeEnabledFor('7d', custom)).toBe(true);
    expect(metricsRangeEnabledFor('30m', custom)).toBe(false);
    expect(metricsRangeEnabledFor('2h', custom)).toBe(false);
  });

  it('every tab\'s defaultWindowS is on its OWN ladder', () => {
    for (const tab of [...METRICS_CENTRAL_ENTRIES, ...METRICS_CUSTOM_ENTRIES]) {
      expect(metricsRangeEnabledFor(tab.defaultWindowS, tab)).toBe(true);
    }
  });

  it('the custom ladder maps ONLY onto the frozen ResourcesWindow union', () => {
    expect(metricsResourcesWindow('24h')).toBe('24h');
    expect(metricsResourcesWindow('7d')).toBe('7d');
    expect(metricsResourcesWindow('15m')).toBe('15m');
    expect(metricsResourcesWindow('1h')).toBe('1h');
    // The central-only options are NOT on the resources ladder — the
    // mapping refuses them (the door would 400 on them).
    expect(metricsResourcesWindow('30m')).toBeNull();
    expect(metricsResourcesWindow('2h')).toBeNull();
  });

  it('the central ladder\'s seconds are derived from the ladder', () => {
    expect(metricsCentralWindowSeconds('15m')).toBe(900);
    expect(metricsCentralWindowSeconds('1h')).toBe(3600);
    expect(metricsCentralWindowSeconds('2h')).toBe(7200);
    expect(metricsCentralWindowSeconds('24h')).toBeNull();
  });
});

describe('the range domain — the KEEP-OR-SNAP rule (3.2)', () => {
  it('KEEPS the window when the new tab serves it', () => {
    const resolution = resolveWindowOnTabChange('2h', metricsCentralTab('jobs'));
    expect(resolution.window).toBe('2h');
    expect(resolution.snapped).toBe(false);
  });

  it('SNAPS to the new tab\'s default when the tab cannot serve the window', () => {
    const resolution = resolveWindowOnTabChange('2h', metricsCustomTab());
    expect(resolution.window).toBe(metricsCustomTab().defaultWindowS);
    expect(resolution.snapped).toBe(true);
  });

  it('SNAPS back from a resources-only window onto the central default', () => {
    const resolution = resolveWindowOnTabChange('7d', metricsCentralTab('mcp'));
    expect(resolution.window).toBe(metricsCentralTab('mcp').defaultWindowS);
    expect(resolution.snapped).toBe(true);
  });

  it('the snap is ANNOUNCED — never silent', () => {
    const note = metricsSnapNote('1h', 'Resources');
    expect(note).toContain('1h');
    expect(note).toContain('Resources');
  });
});

describe('the range domain — the REQUEST BUILDER (3.5b)', () => {
  it('asks for W when the baseline is off', () => {
    expect(metricsSeriesWindowS(3600, false)).toBe(3600);
  });

  it('asks for 2W when the baseline is on — the SAME request, never a second one', () => {
    expect(metricsSeriesWindowS(3600, true)).toBe(7200);
    expect(metricsSeriesWindowS(900, true)).toBe(1800);
  });

  it('NEVER emits a window above the substrate ceiling', () => {
    expect(metricsSeriesWindowS(7200, true)).toBe(METRICS_WINDOW_CEILING_S);
    expect(metricsSeriesWindowS(100000, true)).toBe(METRICS_WINDOW_CEILING_S);
    expect(metricsSeriesWindowS(100000, false)).toBe(METRICS_WINDOW_CEILING_S);
    expect(metricsSeriesWindowS(METRICS_WINDOW_CEILING_S, true)).toBe(METRICS_WINDOW_CEILING_S);
  });

  it('the ceiling is the door\'s own window target (7200 s)', () => {
    expect(METRICS_WINDOW_CEILING_S).toBe(7200);
  });

  it('the baseline\'s guard is W <= 1 h', () => {
    expect(METRICS_BASELINE_MAX_WINDOW_S).toBe(3600);
  });
});

describe('the range domain — the SERVER-ANCHORED SPLIT (3.4)', () => {
  it('splits the payload at ITS OWN midpoint', () => {
    const split = splitSeriesAtMidpoint(payload({ series: [series('m', 4, 1800)] }), 3600);
    expect(split).not.toBeNull();
    expect(split!.midpointMs).toBe(Date.parse('2026-11-03T01:00:00+00:00'));
    expect(split!.spanS).toBe(7200);
    // The four points sit at 00:00, 00:30, 01:00, 01:30 — the first two
    // are the previous window, the last two the current one.
    expect(split!.previous[0].points.length).toBe(2);
    expect(split!.current[0].points.length).toBe(2);
  });

  it('a FAKED CLIENT-CLOCK OFFSET leaves the split point UNCHANGED', () => {
    const baseline = splitSeriesAtMidpoint(payload({ series: [series('m', 4, 1800)] }), 3600);
    const realNow = Date.now;
    try {
      // The client clock is 10 minutes fast — the split must not move.
      Date.now = () => realNow() + 600_000;
      const skewed = splitSeriesAtMidpoint(payload({ series: [series('m', 4, 1800)] }), 3600);
      expect(skewed!.midpointMs).toBe(baseline!.midpointMs);
      expect(skewed!.current[0].points).toEqual(baseline!.current[0].points);
      expect(skewed!.previous[0].points).toEqual(baseline!.previous[0].points);
    } finally {
      Date.now = realNow;
    }
  });

  it('REFUSES to split a CLAMPED payload (the door clamped it and answered 200)', () => {
    // The door clamped the 2 h request to 1 h and said nothing.
    const clamped = payload({
      window: {
        from_utc: '2026-11-03T00:00:00+00:00',
        to_utc: '2026-11-03T01:00:00+00:00',
        interval_s: 15,
        interval_s_source: 'observed',
      },
      series: [series('m', 4, 1800)],
    });
    expect(splitSeriesAtMidpoint(clamped, 3600)).toBeNull();
  });

  it('refuses a payload with an unusable window', () => {
    const broken = payload({
      window: {
        from_utc: 'not-a-timestamp',
        to_utc: 'not-a-timestamp',
        interval_s: 15,
        interval_s_source: 'observed',
      },
    });
    expect(splitSeriesAtMidpoint(broken, 3600)).toBeNull();
  });
});

describe('the range domain — the DERIVED interval (3.11 / AM-18)', () => {
  it('reports the SPAN ÷ POINT COUNT — not the door\'s observed cadence', () => {
    // 2 h over 240 points ⇒ 30 s apart, while the door's observed
    // cadence is 15 s.
    const withPoints = payload({ series: [series('m', 240)] });
    expect(metricsDerivedIntervalS(withPoints)).toBe(30);
    expect(withPoints.window.interval_s).toBe(15);
  });

  it('reports nothing without a payload or without points', () => {
    expect(metricsDerivedIntervalS(null)).toBeNull();
    expect(metricsDerivedIntervalS(payload())).toBeNull();
  });

  it('counts the payload\'s points', () => {
    expect(metricsPayloadPointCount(payload({ series: [series('m', 7)] }))).toBe(7);
    expect(metricsPayloadPointCount(null)).toBe(0);
  });
});

describe('the range domain — the COPY (the frame\'s states)', () => {
  it('the custom slot\'s baseline reason names the resources ladder', () => {
    expect(METRICS_BASELINE_CUSTOM_SLOT_REASON).toContain('15m/1h/24h/7d');
    expect(METRICS_BASELINE_CUSTOM_SLOT_REASON).toContain('no previous-window offset');
  });

  it('the baseline\'s range reason states the 1 h guard', () => {
    expect(METRICS_BASELINE_RANGE_REASON).toContain('1 h or less');
  });

  it('the clamped reason states that the previous window is unavailable', () => {
    expect(METRICS_BASELINE_CLAMPED_REASON).toContain('clamped');
  });

  it('the fidelity disclosure names the three limits (3.8)', () => {
    expect(METRICS_BASELINE_FIDELITY_DISCLOSURE).toContain('bucket can straddle the split');
    expect(METRICS_BASELINE_FIDELITY_DISCLOSURE).toContain('eviction edge');
    expect(METRICS_BASELINE_FIDELITY_DISCLOSURE).toContain('resolution halves');
  });

  it('the frame\'s range note says the panel has its own control (AM-19)', () => {
    expect(METRICS_RANGE_CUSTOM_SLOT_NOTE).toContain('own range control');
  });
});

describe('the range domain — the manifest owns the defaults', () => {
  it('the central tabs open on 1 h and the custom module on 1 h', () => {
    for (const tab of METRICS_CENTRAL_ENTRIES) {
      expect(tab.defaultWindowS).toBe('1h');
    }
    expect(metricsCustomTab().defaultWindowS).toBe('1h');
  });

  it('a tab entry WITHOUT a default is a type error, not a runtime surprise', () => {
    // The manifest's type demands the member; the runtime mirror proves
    // the shape is carried by every entry.
    const entries: readonly MetricsTabEntry[] = [
      ...METRICS_CENTRAL_ENTRIES,
      ...METRICS_CUSTOM_ENTRIES,
    ];
    for (const entry of entries) {
      expect(entry.defaultWindowS).toBeTruthy();
    }
  });
});
