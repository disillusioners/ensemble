// The shared SVG chart primitive spec (P5 task 5.3/5.8 · D1 pack
// `metrics-fe-svg-chart`).
//
// The pure scale/path helpers are asserted DIRECTLY (the house
// `svg-timeline:88-114` pattern) — the component owns no HTTP and no
// polling, so its geometry is unit-testable without a DOM.
//
// The load-bearing pin is P5-M6: the value→y scale NORMALISES against
// the series' own observed [min,max]. Removing that normalisation (a
// raw value, or a fixed domain) turns this spec RED.

import {
  CHART_PLOT_BOTTOM,
  CHART_PLOT_LEFT,
  CHART_PLOT_RIGHT,
  CHART_PLOT_TOP,
  CHART_TICK_COUNT,
  buildChartAreaPath,
  buildChartLinePath,
  buildChartPath,
  buildChartStepPath,
  chartCoords,
  chartPointTooltip,
  chartSeriesDomain,
  chartUtcTicks,
  chartXScale,
  chartYScale,
  formatChartUtc,
  formatChartValue,
} from './svg-line-chart.component';

const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);
const T1 = Date.UTC(2026, 10, 2, 10, 0, 0);

describe('svg-line-chart — the x scale', () => {
  it('maps the window start to the plot left and the end to the plot right', () => {
    expect(chartXScale(T0, T0, T1)).toBeCloseTo(CHART_PLOT_LEFT, 6);
    expect(chartXScale(T1, T0, T1)).toBeCloseTo(CHART_PLOT_RIGHT, 6);
  });

  it('maps the midpoint to the middle of the plot', () => {
    const mid = chartXScale(T0 + (T1 - T0) / 2, T0, T1);
    expect(mid).toBeCloseTo((CHART_PLOT_LEFT + CHART_PLOT_RIGHT) / 2, 6);
  });

  it('clamps a sample outside the window to the plot edge', () => {
    expect(chartXScale(T0 - 10_000, T0, T1)).toBe(CHART_PLOT_LEFT);
    expect(chartXScale(T1 + 10_000, T0, T1)).toBe(CHART_PLOT_RIGHT);
  });

  it('degenerates to the plot midpoint when the window is empty', () => {
    expect(chartXScale(T0, T0, T0)).toBeCloseTo(
      CHART_PLOT_LEFT + (CHART_PLOT_RIGHT - CHART_PLOT_LEFT) / 2,
      6,
    );
  });

  it('falls back to the plot left for a non-finite sample', () => {
    expect(chartXScale(Number.NaN, T0, T1)).toBe(CHART_PLOT_LEFT);
  });
});

describe('svg-line-chart — the y scale (P5-M6: the per-series normalisation)', () => {
  it('maps the series minimum to the floor and the maximum to the ceiling', () => {
    expect(chartYScale(10, 10, 20)).toBeCloseTo(CHART_PLOT_BOTTOM, 6);
    expect(chartYScale(20, 10, 20)).toBeCloseTo(CHART_PLOT_TOP, 6);
  });

  it('maps the midpoint of the range to the middle of the plot', () => {
    const mid = chartYScale(15, 10, 20);
    expect(mid).toBeCloseTo((CHART_PLOT_TOP + CHART_PLOT_BOTTOM) / 2, 6);
  });

  it('NORMALISES against the range — the same value maps differently under different ranges', () => {
    // 15 is the midpoint of [10,20] and the minimum of [15,25]: a scale
    // that ignored the normalisation would return the same y for both.
    const underWide = chartYScale(15, 10, 20);
    const underNarrow = chartYScale(15, 15, 25);
    expect(underWide).not.toBeCloseTo(underNarrow, 3);
  });

  it('clamps a value outside the observed range to the plot edge', () => {
    expect(chartYScale(0, 10, 20)).toBeCloseTo(CHART_PLOT_BOTTOM, 6);
    expect(chartYScale(100, 10, 20)).toBeCloseTo(CHART_PLOT_TOP, 6);
  });

  it('degenerates to the plot middle when the range is empty', () => {
    expect(chartYScale(5, 5, 5)).toBeCloseTo(
      CHART_PLOT_TOP + (CHART_PLOT_BOTTOM - CHART_PLOT_TOP) / 2,
      6,
    );
  });

  it('falls back to the floor for a non-finite value', () => {
    expect(chartYScale(Number.NaN, 10, 20)).toBe(CHART_PLOT_BOTTOM);
  });
});

describe('svg-line-chart — the series domain', () => {
  it('reads the observed min and max of a series', () => {
    const domain = chartSeriesDomain([
      { t: T0, v: 4 },
      { t: T1, v: 12 },
      { t: T0 + 1000, v: -1 },
    ]);
    expect(domain.min).toBe(-1);
    expect(domain.max).toBe(12);
  });

  it('ignores the non-finite samples', () => {
    const domain = chartSeriesDomain([
      { t: T0, v: Number.NaN },
      { t: T1, v: 7 },
    ]);
    expect(domain.min).toBe(7);
    expect(domain.max).toBe(7);
  });

  it('returns a zero range for an empty series', () => {
    expect(chartSeriesDomain([])).toEqual({ min: 0, max: 0 });
  });
});

describe('svg-line-chart — the paths', () => {
  const coords = [
    { x: 10, y: 100 },
    { x: 20, y: 50 },
    { x: 30, y: 75 },
  ];

  it('builds a linear path with M + L commands', () => {
    const path = buildChartLinePath(coords);
    expect(path.startsWith('M10.00,100.00')).toBe(true);
    expect(path).toContain('L20.00,50.00');
    expect(path).not.toContain('H');
    expect(path).not.toContain('V');
  });

  it('builds a STEP path with horizontal-then-vertical commands', () => {
    const path = buildChartStepPath(coords);
    expect(path.startsWith('M10.00,100.00')).toBe(true);
    expect(path).toContain('H20.00');
    expect(path).toContain('V50.00');
    expect(path).toContain('H30.00');
    expect(path).toContain('V75.00');
  });

  it('builds an EMPTY path for an empty series', () => {
    expect(buildChartLinePath([])).toBe('');
    expect(buildChartStepPath([])).toBe('');
    expect(buildChartAreaPath([], 'line')).toBe('');
  });

  it('dispatches the path by the rendering mode', () => {
    expect(buildChartPath(coords, 'line')).toBe(buildChartLinePath(coords));
    expect(buildChartPath(coords, 'step')).toBe(buildChartStepPath(coords));
    expect(buildChartPath(coords, 'step')).not.toBe(buildChartPath(coords, 'line'));
  });

  it('closes the area path to the plot floor', () => {
    const area = buildChartAreaPath(coords, 'line');
    expect(area.endsWith('Z')).toBe(true);
    expect(area).toContain(String(CHART_PLOT_BOTTOM));
  });

  it('closes a STEP area path with the step geometry', () => {
    const area = buildChartAreaPath(coords, 'step');
    expect(area).toContain('H20.00');
    expect(area.endsWith('Z')).toBe(true);
  });
});

describe('svg-line-chart — the coordinates and the axis', () => {
  it('projects the samples onto the plot', () => {
    const projected = chartCoords(
      [
        { t: T0, v: 0 },
        { t: T1, v: 10 },
      ],
      T0,
      T1,
      0,
      10,
    );
    expect(projected).toHaveLength(2);
    expect(projected[0].x).toBeCloseTo(CHART_PLOT_LEFT, 6);
    expect(projected[1].x).toBeCloseTo(CHART_PLOT_RIGHT, 6);
  });

  it('drops the non-finite samples from the projection', () => {
    const projected = chartCoords(
      [
        { t: T0, v: 1 },
        { t: Number.NaN, v: 2 },
        { t: T1, v: Number.NaN },
      ],
      T0,
      T1,
      0,
      10,
    );
    expect(projected).toHaveLength(1);
  });

  it('lays the ticks out across the window', () => {
    const ticks = chartUtcTicks(T0, T1);
    expect(ticks).toHaveLength(CHART_TICK_COUNT);
    expect(ticks[0].x).toBeCloseTo(CHART_PLOT_LEFT, 6);
    expect(ticks[ticks.length - 1].x).toBeCloseTo(CHART_PLOT_RIGHT, 6);
  });

  it('labels the ticks in UTC, never in local time', () => {
    const ticks = chartUtcTicks(T0, T1);
    expect(ticks[0].label).toBe('09:00');
    expect(ticks[ticks.length - 1].label).toBe('10:00');
    expect(formatChartUtc(T0)).toBe('09:00');
  });

  it('returns no ticks for an empty window', () => {
    expect(chartUtcTicks(T0, T0)).toEqual([]);
    expect(chartUtcTicks(T0, T1, 0)).toEqual([]);
  });
});

describe('svg-line-chart — the value labels', () => {
  it('keeps an integer an integer', () => {
    expect(formatChartValue(4)).toBe('4');
  });

  it('rounds a fractional value to two decimals', () => {
    expect(formatChartValue(4.125)).toBe('4.13');
  });

  it('renders a non-finite value as the em-dash placeholder', () => {
    expect(formatChartValue(Number.NaN)).toBe('—');
  });

  it('builds a native-tooltip text of the UTC time and the value', () => {
    expect(chartPointTooltip({ t: T0, v: 12 })).toBe('09:00 UTC · 12');
  });
});
