// The CONSOLIDATED house SVG engine spec (T1 phase 4, task 4.1 / 4.2 / 4.3).
//
// ── WHAT THIS SPEC IS ───────────────────────────────────────────────────
//
// The TWO former engines' assertions, ported (the pure helpers) and
// REWRITTEN (the component-level ones — the two component APIs differ,
// so those assertions are new specs against the consolidated API, never
// relocated ones). The per-assertion disposition table lives in
// `.agents/shared/planning/metrics-unification/fe-t1-build/phase4-evidence.md`
// and accounts for EVERY assertion of both former specs (29 + 13 = 42).
//
// The load-bearing pins:
//   * the ported helpers keep their NAMES and their behaviour (the
//     audit cited them by name — that property must survive);
//   * the SINGLE-GEOMETRY invariant (the older engine's constants are
//     aliases onto the house ones);
//   * the capability checklist — 6/6 (window ladder, series toggles,
//     anomaly markers, drill-down, UTC formatting, counter STEP);
//   * the mini's closed extension set AND the absence of everything
//     outside it (axes grid / zoom / brush / legend-toggle / export);
//   * R-2 — NO T1 surface renders a mini.

import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import { CHART_SEVERITY_COLORS } from '../chart-theme';
import { metricsCustomTab } from '../../../pages/metrics/tabs.manifest';
import {
  CHART_PLOT_BOTTOM,
  CHART_PLOT_LEFT,
  CHART_PLOT_RIGHT,
  CHART_PLOT_TOP,
  CHART_POINT_RADIUS,
  CHART_TICK_COUNT,
  HOUSE_AXIS_MARGIN,
  HOUSE_GEOMETRY,
  TIMELINE_MARKER_RADIUS,
  TIMELINE_PLOT_LEFT,
  TIMELINE_PLOT_RIGHT,
  TIMELINE_TICK_COUNT,
  buildAreaPath,
  buildChartAreaPath,
  buildChartLinePath,
  buildChartPath,
  buildChartStepPath,
  buildLinePath,
  chartCoords,
  chartPointTooltip,
  chartSeriesDomain,
  chartSharedDomain,
  chartUtcTicks,
  chartXScale,
  chartYScale,
  formatChartUtc,
  formatChartUtcTick,
  formatChartValue,
  formatUtcClock,
  houseCrispStrokeWidth,
  houseGeometryFor,
  houseNearestPointIndex,
  houseViewBox,
  timelineXScale,
  timelineYScale,
} from './house-svg-renderer';
import {
  HouseSvgRendererComponent,
} from './house-svg-renderer.component';

const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);
const T1 = Date.UTC(2026, 10, 2, 10, 0, 0);

const SRC_ROOT = join(__dirname, '..', '..', '..', '..');

// ═══════════════════════════════════════════════════════════════════════
// THE PORTED ASSERTIONS — the newer engine's spec (29 of them)
// ═══════════════════════════════════════════════════════════════════════

describe('house-svg-renderer — the x scale', () => {
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

describe('house-svg-renderer — the y scale (P5-M6: the per-series normalisation)', () => {
  it('maps the series minimum to the floor and the maximum to the ceiling', () => {
    expect(chartYScale(10, 10, 20)).toBeCloseTo(CHART_PLOT_BOTTOM, 6);
    expect(chartYScale(20, 10, 20)).toBeCloseTo(CHART_PLOT_TOP, 6);
  });

  it('maps the midpoint of the range to the middle of the plot', () => {
    const mid = chartYScale(15, 10, 20);
    expect(mid).toBeCloseTo((CHART_PLOT_TOP + CHART_PLOT_BOTTOM) / 2, 6);
  });

  it('NORMALISES against the range — the same value maps differently under different ranges', () => {
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

describe('house-svg-renderer — the series domain', () => {
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

describe('house-svg-renderer — the paths', () => {
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

  it('renders a COUNTER as STEP and a GAUGE as LINEAR (the geometric truth the e2e retired)', () => {
    // The e2e's `d`-ATTRIBUTE assertions are RETIRED — NOT PRESERVED
    // (task 4.10). Under a canvas renderer there is no `d`; the
    // geometry lives HERE, in the renderer spec.
    const step = buildChartPath(coords, 'step');
    const line = buildChartPath(coords, 'line');

    expect(step).toContain('H');
    expect(step).toContain('V');
    // A STEP path never contains a diagonal segment between two samples.
    expect(step).not.toMatch(/L\d+\.\d+,\d+\.\d+.*L\d+\.\d+,\d+\.\d+/);

    expect(line).toContain('L');
    expect(line).not.toContain('H');
    expect(line).not.toContain('V');
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

describe('house-svg-renderer — the coordinates and the axis', () => {
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

describe('house-svg-renderer — the value labels', () => {
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

// ═══════════════════════════════════════════════════════════════════════
// THE RE-POINTED ASSERTIONS — the older engine's PURE helpers (6 of them)
// ═══════════════════════════════════════════════════════════════════════

describe('house-svg-renderer — the older engine\'s ported helpers (re-pointed)', () => {
  it('timelineXScale maps domain endpoints to plot edges and the midpoint to centre', () => {
    const t0 = Date.UTC(2026, 9, 18, 16, 0, 0);
    const t1 = Date.UTC(2026, 9, 18, 17, 0, 0);

    expect(timelineXScale(t0, t0, t1)).toBeCloseTo(TIMELINE_PLOT_LEFT, 6);
    expect(timelineXScale(t1, t0, t1)).toBeCloseTo(TIMELINE_PLOT_RIGHT, 6);
    expect(timelineXScale((t0 + t1) / 2, t0, t1)).toBeCloseTo(
      TIMELINE_PLOT_LEFT + (TIMELINE_PLOT_RIGHT - TIMELINE_PLOT_LEFT) / 2,
      6,
    );
  });

  it('timelineXScale degenerates to the midpoint when the domain has no span', () => {
    const t = Date.UTC(2026, 9, 18, 16, 0, 0);
    expect(timelineXScale(t, t, t)).toBeCloseTo(
      TIMELINE_PLOT_LEFT + (TIMELINE_PLOT_RIGHT - TIMELINE_PLOT_LEFT) / 2,
      6,
    );
  });

  it('timelineYScale maps min to the floor and max to the ceiling', () => {
    expect(timelineYScale(100, 100, 200)).toBeCloseTo(CHART_PLOT_BOTTOM, 6);
    expect(timelineYScale(200, 100, 200)).toBeCloseTo(CHART_PLOT_TOP, 6);
  });

  it('buildLinePath / buildAreaPath emit non-empty path data', () => {
    const coords = [
      { x: 10, y: 20 },
      { x: 30, y: 40 },
      { x: 50, y: 25 },
    ];
    const line = buildLinePath(coords);
    const area = buildAreaPath(coords);

    expect(line.length).toBeGreaterThan(0);
    expect(line.startsWith('M')).toBe(true);
    expect(line).toContain('L');
    expect(area.length).toBeGreaterThan(0);
    expect(area.endsWith('Z')).toBe(true);
    expect(buildLinePath([])).toBe('');
    expect(buildAreaPath([])).toBe('');
  });

  it('formatUtcClock formats in UTC regardless of the process timezone', () => {
    const ms = Date.UTC(2026, 9, 18, 23, 45, 0);
    expect(formatUtcClock(ms)).toBe('23:45');
  });

  it('keeps ONE geometry — the older engine\'s constants alias the house ones', () => {
    // The consolidation's whole point: the older engine's plot box is the
    // house plot box, not a second one.
    expect(TIMELINE_PLOT_LEFT).toBe(CHART_PLOT_LEFT);
    expect(TIMELINE_PLOT_RIGHT).toBe(CHART_PLOT_RIGHT);
    expect(TIMELINE_TICK_COUNT).toBe(CHART_TICK_COUNT);
    expect(timelineXScale).toBe(chartXScale);
    expect(timelineYScale).toBe(chartYScale);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// THE REWRITTEN ASSERTIONS — the older engine's COMPONENT assertions
// (7 of them) — against the consolidated component's API
// ═══════════════════════════════════════════════════════════════════════

function series(id: string, mode: 'line' | 'step' = 'line') {
  return {
    id,
    label: id,
    color: '#38bdf8',
    mode,
    points: [
      { t: T0, v: 1 },
      { t: T1, v: 4 },
    ],
  };
}

describe('HouseSvgRendererComponent — the rewritten component assertions', () => {
  let fixture: ComponentFixture<HouseSvgRendererComponent>;
  let component: HouseSvgRendererComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(HouseSvgRendererComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => fixture.destroy());

  it('renders a non-empty line + area per given series', () => {
    fixture.componentRef.setInput('series', [series('a'), series('b')]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.detectChanges();

    const lines = fixture.debugElement.queryAll(By.css('[data-test="house-series-line"]'));
    const areas = fixture.debugElement.queryAll(By.css('[data-test="house-series-area"]'));
    expect(lines.length).toBe(2);
    expect(areas.length).toBe(2);
    for (const line of lines) {
      expect((line.nativeElement.getAttribute('d') ?? '').length).toBeGreaterThan(0);
    }
    for (const area of areas) {
      expect((area.nativeElement.getAttribute('d') ?? '').length).toBeGreaterThan(0);
    }
  });

  it('renders only the series it is given (the toggle lives in the parent)', () => {
    fixture.componentRef.setInput('series', [series('only')]);
    fixture.detectChanges();
    const lines = fixture.debugElement.queryAll(By.css('[data-test="house-series-line"]'));
    expect(lines.length).toBe(1);
    expect(lines[0].nativeElement.getAttribute('data-series')).toBe('only');
  });

  it('positions the anomaly markers by their timestamp', () => {
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('anomalies', [
      {
        anomalyId: 'anom-1',
        seriesId: 'a',
        severity: 'high',
        tsMs: T0 + (T1 - T0) / 2,
        title: 'anomaly',
      },
    ]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.detectChanges();

    const dots = fixture.debugElement.queryAll(By.css('[data-test="house-anomaly-marker-dot"]'));
    expect(dots.length).toBe(1);
    const cx = Number(dots[0].nativeElement.getAttribute('cx'));
    expect(cx).toBeCloseTo(CHART_PLOT_LEFT + (CHART_PLOT_RIGHT - CHART_PLOT_LEFT) / 2, 6);
  });

  it('renders UTC time-axis tick labels (the trend variant)', () => {
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.detectChanges();

    const ticks = fixture.debugElement.queryAll(By.css('[data-test="house-tick"]'));
    expect(ticks.map((t) => t.nativeElement.textContent.trim())).toEqual([
      '09:00',
      '09:15',
      '09:30',
      '09:45',
      '10:00',
    ]);
  });

  it('hands the anomaly over on a marker click (the drill-down hand-off)', () => {
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('anomalies', [
      { anomalyId: 'anom-1', seriesId: 'a', severity: 'high', tsMs: T0, title: 'anomaly' },
    ]);
    fixture.detectChanges();

    const emitted: unknown[] = [];
    fixture.componentRef.setInput('onAnomalyClick', (marker: unknown) => emitted.push(marker));

    const marker = fixture.debugElement.query(By.css('[data-test="house-anomaly-marker"]'));
    marker.nativeElement.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(emitted.length).toBe(1);
    expect((emitted[0] as { anomalyId?: string }).anomalyId).toBe('anom-1');
  });

  it('shows the empty label when there are no samples', () => {
    fixture.detectChanges();
    const empty = fixture.debugElement.query(By.css('[data-test="house-empty"]'));
    expect(empty).not.toBeNull();
  });

  it('keeps the viewBox intact (never distorted by preserveAspectRatio=none)', () => {
    fixture.detectChanges();
    const svg = fixture.debugElement.query(By.css('[data-test="house-svg"]'));
    expect(svg.nativeElement.getAttribute('viewBox')).toBe(houseViewBox(HOUSE_GEOMETRY));
    expect(svg.nativeElement.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// THE CAPABILITY CHECKLIST — 6/6 (task 4.2)
// ═══════════════════════════════════════════════════════════════════════

describe('house-svg-renderer — the capability checklist (6/6)', () => {
  it('(1) the window ladder is present (the resource timeline owns 4 windows)', () => {
    // T1 phase 5 (task 5.13): the ladder's SSOT moved to the manifest's
    // CUSTOM entry (the frozen `ResourcesWindow` union) — the relocated
    // timeline panel carries no window-ladder constant of its own. The
    // capability is re-read from the manifest, the same 4 windows.
    expect(metricsCustomTab().allowedWindows).toEqual(['15m', '1h', '24h', '7d']);
  });

  it('(2) the series toggle capability is present (the renderer draws what it is given)', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('series', [series('a'), series('b')]);
    fixture.detectChanges();
    expect(fixture.componentInstance.rendered().map((entry) => entry.id)).toEqual(['a', 'b']);
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.detectChanges();
    expect(fixture.componentInstance.rendered().map((entry) => entry.id)).toEqual(['a']);
    fixture.destroy();
  });

  it('(3) the anomaly markers are present, with the house severity colours VERBATIM', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('anomalies', [
      { anomalyId: 'a1', seriesId: 'a', severity: 'low', tsMs: T0, title: 'low' },
      { anomalyId: 'a2', seriesId: 'a', severity: 'medium', tsMs: T0, title: 'medium' },
      { anomalyId: 'a3', seriesId: 'a', severity: 'high', tsMs: T0, title: 'high' },
    ]);
    fixture.detectChanges();
    const dots = fixture.debugElement.queryAll(By.css('[data-test="house-anomaly-marker-dot"]'));
    expect(dots.map((dot) => dot.nativeElement.getAttribute('fill'))).toEqual([
      CHART_SEVERITY_COLORS.low,
      CHART_SEVERITY_COLORS.medium,
      CHART_SEVERITY_COLORS.high,
    ]);
    expect(CHART_SEVERITY_COLORS).toEqual({ low: '#38bdf8', medium: '#f59e0b', high: '#ef4444' });
    fixture.destroy();
  });

  it('(4) the drill-down capability is present (the marker click hands the anomaly over)', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('anomalies', [
      { anomalyId: 'anom-9', seriesId: 'a', severity: 'high', tsMs: T0, title: 'anomaly' },
    ]);
    fixture.detectChanges();
    const emitted: { anomalyId?: string }[] = [];
    fixture.componentRef.setInput('onAnomalyClick', (marker: { anomalyId?: string }) =>
      emitted.push(marker),
    );
    fixture.debugElement
      .query(By.css('[data-test="house-anomaly-marker"]'))
      .nativeElement.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(emitted.map((event) => event.anomalyId)).toEqual(['anom-9']);
    fixture.destroy();
  });

  it('(5) the UTC formatting is present (never local time)', () => {
    expect(formatChartUtc(Date.UTC(2026, 9, 18, 23, 45, 0))).toBe('23:45');
    expect(formatUtcClock(Date.UTC(2026, 9, 18, 23, 45, 0))).toBe('23:45');
    expect(chartUtcTicks(T0, T1)[0].label).toBe('09:00');
  });

  it('(6) the counter STEP rendering is present', () => {
    const coords = [
      { x: 10, y: 100 },
      { x: 20, y: 50 },
    ];
    expect(buildChartStepPath(coords)).toContain('H20.00');
    expect(buildChartStepPath(coords)).toContain('V50.00');
    expect(buildChartPath(coords, 'step')).toBe(buildChartStepPath(coords));
  });
});

// ═══════════════════════════════════════════════════════════════════════
// THE MINI EXTENSIONS — the CLOSED set (task 4.3)
// ═══════════════════════════════════════════════════════════════════════

describe('house-svg-renderer — the mini extensions (ADJ-ii, the closed set)', () => {
  it('(1) the devicePixelRatio crispness divides the stroke by the ratio', () => {
    expect(houseCrispStrokeWidth(1)).toBeCloseTo(1.6, 6);
    expect(houseCrispStrokeWidth(2)).toBeCloseTo(0.8, 6);
    // A nonsense ratio falls back to the reference density.
    expect(houseCrispStrokeWidth(0)).toBeCloseTo(1.6, 6);
    expect(houseCrispStrokeWidth(Number.NaN)).toBeCloseTo(1.6, 6);
  });

  it('(1b) the component applies the crispness rule to its stroke width', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('devicePixelRatio', 2);
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.detectChanges();
    const line = fixture.debugElement.query(By.css('[data-test="house-series-line"]'));
    expect(Number(line.nativeElement.getAttribute('stroke-width'))).toBeCloseTo(0.8, 6);
    fixture.destroy();
  });

  it('(2) the nearest-point bisect finds the closest sample', () => {
    const times = [0, 10, 20, 30];
    expect(houseNearestPointIndex(times, 19)).toBe(2);
    expect(houseNearestPointIndex(times, 21)).toBe(2);
    expect(houseNearestPointIndex(times, 0)).toBe(0);
    expect(houseNearestPointIndex(times, 999)).toBe(3);
    expect(houseNearestPointIndex([], 5)).toBe(-1);
    expect(houseNearestPointIndex(times, Number.NaN)).toBe(-1);
  });

  it('(2b) the hover overlay reports the nearest sample', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.detectChanges();

    const overlay = fixture.debugElement.query(By.css('[data-test="house-hover-overlay"]'));
    expect(overlay).not.toBeNull();

    // jsdom reports a zero-width rect — the handler must refuse rather
    // than divide by zero.
    overlay.nativeElement.dispatchEvent(
      new MouseEvent('pointermove', { clientX: 10, clientY: 10, bubbles: true }),
    );
    expect(fixture.componentInstance.hovered()).toBeNull();
    fixture.destroy();
  });

  it('(3) the last-value marker is drawn per series', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('series', [series('a'), series('b')]);
    fixture.detectChanges();
    const marks = fixture.debugElement.queryAll(By.css('[data-test="house-last-value"]'));
    expect(marks.length).toBe(2);
    expect(marks[0].nativeElement.getAttribute('data-series')).toBe('a');
    fixture.destroy();
  });

  it('(4) the optional baseline line is drawn when a baseline is given', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.detectChanges();
    expect(fixture.debugElement.queryAll(By.css('[data-test="house-baseline"]')).length).toBe(0);

    fixture.componentRef.setInput('baseline', [series('baseline')]);
    fixture.detectChanges();
    const baselines = fixture.debugElement.queryAll(By.css('[data-test="house-baseline"]'));
    expect(baselines.length).toBe(1);
    expect((baselines[0].nativeElement.getAttribute('d') ?? '').length).toBeGreaterThan(0);
    fixture.destroy();
  });

  it('(5) the viewBox follows the measured container', () => {
    const house = houseGeometryFor(0, 0);
    expect(house.viewHeight).toBe(HOUSE_GEOMETRY.viewHeight);
    expect(houseViewBox(house)).toBe(`0 0 ${HOUSE_GEOMETRY.viewWidth} ${HOUSE_GEOMETRY.viewHeight}`);

    // A container twice as wide as tall doubles the viewBox height (the
    // drawing fills its container instead of being letterboxed).
    const wide = houseGeometryFor(900, 520);
    expect(wide.viewHeight).toBe(520);
    expect(wide.plotBottom).toBe(520 - HOUSE_AXIS_MARGIN);

    // The height is bounded — the drawing never runs away.
    expect(houseGeometryFor(100, 5000).viewHeight).toBe(HOUSE_GEOMETRY.viewHeight * 2);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// THE SCOPE GUARD — the ABSENCE of everything outside the closed set
// ═══════════════════════════════════════════════════════════════════════

describe('house-svg-renderer — the scope guard (the out-of-set capabilities are ABSENT)', () => {
  it('the MINI role renders no axes grid, no ticks and no legend', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(HouseSvgRendererComponent);
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('variant', 'mini');
    fixture.detectChanges();

    expect(fixture.debugElement.queryAll(By.css('[data-test="house-grid"]')).length).toBe(0);
    expect(fixture.debugElement.queryAll(By.css('[data-test="house-tick"]')).length).toBe(0);
    expect(fixture.debugElement.queryAll(By.css('[data-test="house-legend"]')).length).toBe(0);
    expect(fixture.debugElement.queryAll(By.css('[data-test="house-axis-caption"]')).length).toBe(0);
    fixture.destroy();
  });

  it('NEITHER role renders a zoom, a brush, an export or a legend toggle', async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    for (const variant of ['mini', 'trend'] as const) {
      const fixture = TestBed.createComponent(HouseSvgRendererComponent);
      fixture.componentRef.setInput('series', [series('a')]);
      fixture.componentRef.setInput('variant', variant);
      fixture.detectChanges();
      const host: HTMLElement = fixture.nativeElement;
      expect(host.querySelectorAll('[data-test*="zoom"]').length).toBe(0);
      expect(host.querySelectorAll('[data-test*="brush"]').length).toBe(0);
      expect(host.querySelectorAll('[data-test*="export"]').length).toBe(0);
      expect(host.querySelectorAll('button').length).toBe(0);
      expect(host.querySelectorAll('input[type="checkbox"]').length).toBe(0);
      fixture.destroy();
    }
  });

  it('the engine source carries no zoom / brush / pan / export capability', () => {
    const raw = readFileSync(join(__dirname, 'house-svg-renderer.component.ts'), 'utf8');
    // The COMMENTS are stripped first — the file's own doc comment NAMES
    // the forbidden capabilities, so a naive substring scan would read
    // the prohibition as the capability.
    const source = raw
      .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
      .replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1');
    // The capability TOKENS (not bare substrings — `<span` contains "pan").
    for (const token of ['zoom', 'brush', 'drag', 'exportTo', 'download', 'onPan']) {
      expect(source.toLowerCase().includes(token.toLowerCase())).toBe(false);
    }
  });

  it('R-2: NO T1 page surface renders a mini', () => {
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (
          (entry.name.endsWith('.ts') && !entry.name.endsWith('.spec.ts')) ||
          entry.name.endsWith('.html')
        ) {
          const source = readFileSync(full, 'utf8');
          // A mini is rendered when a page ASKS for the mini role: the
          // `sparkline` intent in a request, or the mini variant in a
          // template. A renderer's own capability check (`intent !==
          // 'sparkline'`) is not a request.
          if (/intent\s*:\s*'sparkline'|variant\s*=\s*"mini"/.test(source)) {
            offenders.push(full.replace(SRC_ROOT, 'frontend/src'));
          }
        }
      }
    };
    walk(join(SRC_ROOT, 'app', 'pages'));
    expect(offenders).toEqual([]);
  });

  // NOTE: the "exactly ONE house SVG engine remains" assertion is a
  // MIGRATION gate — it can only pass once the superseded engines are
  // deleted — so it lives in its own spec
  // (`house-svg-engine-uniqueness.spec.ts`).
});

// ═══════════════════════════════════════════════════════════════════════
// The day-disambiguation extension — the tick beyond 24 h
// ═══════════════════════════════════════════════════════════════════════

describe('house-svg-renderer — the UTC tick disambiguates days', () => {
  it('keeps the compact HH:MM label for a window shorter than a day', () => {
    expect(formatChartUtcTick(T0, 60 * 60 * 1000)).toBe('09:00');
  });

  it('carries the UTC DATE for a window of a day or more', () => {
    const dayStart = Date.UTC(2026, 10, 2, 9, 0, 0);
    expect(formatChartUtcTick(dayStart, 24 * 60 * 60 * 1000)).toBe('11-02 09:00');
    expect(formatChartUtcTick(dayStart, 7 * 24 * 60 * 60 * 1000)).toBe('11-02 09:00');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// PHASE 6 (tasks 6.1 / 6.2) — the SHARED PANEL DOMAIN + the unit axis
// ═══════════════════════════════════════════════════════════════════════

describe('house-svg-renderer — the shared panel domain (phase 6, W7)', () => {
  let fixture: ComponentFixture<HouseSvgRendererComponent>;
  let component: HouseSvgRendererComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(HouseSvgRendererComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => fixture.destroy());

  it('chartSharedDomain reads the [min,max] across EVERY series', () => {
    const domain = chartSharedDomain([
      { points: [{ t: 0, v: 10 }, { t: 1, v: 20 }] },
      { points: [{ t: 0, v: 1000 }, { t: 1, v: 2000 }] },
    ]);
    expect(domain.min).toBe(10);
    expect(domain.max).toBe(2000);
  });

  it('chartSharedDomain includes an empty series without poisoning the range', () => {
    const domain = chartSharedDomain([
      { points: [{ t: 0, v: 5 }] },
      { points: [] },
    ]);
    expect(domain.min).toBe(5);
    expect(domain.max).toBe(5);
    expect(chartSharedDomain([])).toEqual({ min: 0, max: 0 });
    expect(chartSharedDomain([{ points: [{ t: 0, v: Number.NaN }] }])).toEqual({ min: 0, max: 0 });
  });

  it('plots BOTH series against the ONE shared domain — no per-series rescale (the W7 numeric assertion)', async () => {
    // Two magnitudes an order apart: a 10–20 series and a 1000–2000
    // series. Under the OLD per-series normalisation BOTH filled the
    // plot; under the shared panel domain the small series must sit in
    // the bottom sliver of the SAME range.
    const small = {
      id: 'small',
      label: 'small',
      color: '#38bdf8',
      mode: 'line' as const,
      points: [
        { t: T0, v: 10 },
        { t: T1, v: 20 },
      ],
    };
    const large = {
      id: 'large',
      label: 'large',
      color: '#f59e0b',
      mode: 'line' as const,
      points: [
        { t: T0, v: 1000 },
        { t: T1, v: 2000 },
      ],
    };
    fixture.componentRef.setInput('series', [small, large]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.detectChanges();

    // The panel domain is the JOINT range — asserted on the component.
    expect(component.panelDomain()).toEqual({ min: 10, max: 2000 });

    // THE NUMERIC ASSERTION: a series is independently rescaled iff its
    // points span the FULL plot height while the panel range is wider.
    // The small series' first point (10 = the panel floor) sits at the
    // floor — correct; its SECOND point (20, ~0.5% up the panel range)
    // must sit essentially AT the floor too, NOT at the ceiling.
    const floor = component.geometry().plotBottom;
    const ceiling = component.geometry().plotTop;
    const height = floor - ceiling;
    const smallSecondY = component.rendered().find((entry) => entry.id === 'small')!
      .markers[1].y;
    const heightUsed = (floor - smallSecondY) / height;
    // 20 against the 10–2000 domain ⇒ ~0.5% of the plot — a per-series
    // rescale would put it at 100% (the ceiling). A generous 5% bound
    // still separates the two worlds by an order of magnitude.
    expect(heightUsed).toBeLessThan(0.05);
    // And the large series' max DOES reach the ceiling (the shared
    // domain is not a clamp that flattens everything).
    const largeSecondY = component.rendered().find((entry) => entry.id === 'large')!
      .markers[1].y;
    expect((floor - largeSecondY) / height).toBeGreaterThan(0.95);
  });

  it('the hover projection uses the SAME shared domain (no third rescale path)', async () => {
    const small = {
      id: 'small',
      label: 'small',
      color: '#38bdf8',
      mode: 'line' as const,
      points: [
        { t: T0, v: 10 },
        { t: T1, v: 20 },
      ],
    };
    fixture.componentRef.setInput('series', [small]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.detectChanges();
    // jsdom reports zero rects — stub the SVG's rect so the bisect runs.
    const svg = fixture.debugElement.query(By.css('svg')).nativeElement as HTMLElement;
    const rect = { left: 0, top: 0, width: 900, height: 260 } as DOMRect;
    jest.spyOn(svg, 'getBoundingClientRect').mockReturnValue(rect);
    // Hover over the FIRST sample's x position (plot-fraction → client).
    const firstX = component.rendered()[0].markers[0].x;
    const fraction = (firstX - component.geometry().plotLeft) / component.plotWidth();
    const clientX = rect.left + fraction * rect.width;
    const clientY =
      rect.top + (rect.height * (component.geometry().plotTop + 4)) / component.geometry().viewHeight;
    // The handler binds on the OVERLAY (a child of the svg) — dispatch
    // there; the handler itself reads the SVG's rect (stubbed above).
    const overlay = fixture.debugElement.query(By.css('[data-test="house-hover-overlay"]'))
      .nativeElement as HTMLElement;
    overlay.dispatchEvent(new MouseEvent('pointermove', { clientX, clientY, bubbles: true }));
    const hover = component.hovered();
    expect(hover).not.toBeNull();
    expect(hover!.seriesId).toBe('small');
    // The hover's y equals the rendered marker's y — the SAME shared
    // domain produced both (a per-series rescale in the hover path
    // would drift them apart).
    expect(hover!.y).toBeCloseTo(component.rendered()[0].markers[0].y, 5);
  });

  it('the baseline rides the SAME shared domain (never its own rescale)', async () => {
    const main = series('main');
    const baseline = {
      ...series('prev'),
      points: [
        { t: T0, v: 100 },
        { t: T1, v: 900 },
      ],
    };
    fixture.componentRef.setInput('series', [main]);
    fixture.componentRef.setInput('baseline', [baseline]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.detectChanges();
    // The panel domain spans BOTH the series and the baseline.
    expect(component.panelDomain()).toEqual({ min: 1, max: 900 });
  });
});

describe('HouseSvgRendererComponent — the unit-labeled value axis (phase 6, 6.2)', () => {
  let fixture: ComponentFixture<HouseSvgRendererComponent>;
  let component: HouseSvgRendererComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HouseSvgRendererComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(HouseSvgRendererComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => fixture.destroy());

  it('the TREND role renders the shared domain suffixed with the panel unit', () => {
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.componentRef.setInput('unit', 'MB');
    fixture.detectChanges();

    const ticks = fixture.debugElement.queryAll(By.css('[data-test="house-y-tick"]'));
    expect(ticks.length).toBe(3);
    const texts = ticks.map((tick) => (tick.nativeElement.textContent ?? '').trim());
    // The shared domain of the fixture series is 1–4: floor 1, mid 2.5,
    // ceiling 4 — each with the unit suffix.
    expect(texts).toContain('4 MB');
    expect(texts).toContain('1 MB');
    expect(texts.some((text) => text.endsWith('MB'))).toBe(true);
  });

  it('an unmapped unit renders `unit unknown` — never a guess', () => {
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('variant', 'trend');
    fixture.detectChanges(); // the default unit input is 'unit unknown'
    const texts = fixture.debugElement
      .queryAll(By.css('[data-test="house-y-tick"]'))
      .map((tick) => (tick.nativeElement.textContent ?? '').trim());
    expect(texts.some((text) => text.endsWith('unit unknown'))).toBe(true);
  });

  it('the MINI role renders NO value axis (the R-2 exemption, asserted)', () => {
    fixture.componentRef.setInput('series', [series('a')]);
    fixture.componentRef.setInput('variant', 'mini');
    fixture.componentRef.setInput('unit', 'MB');
    fixture.detectChanges();
    expect(fixture.debugElement.queryAll(By.css('[data-test="house-y-tick"]')).length).toBe(0);
    expect(component.yAxisLabels()).toEqual([]);
  });
});
