import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import {
  Anomaly,
  HistoryPoint,
} from '../../../models/resources.models';
import {
  DEFAULT_TIMELINE_SERIES,
  SvgTimelineComponent,
  TIMELINE_PLOT_BOTTOM,
  TIMELINE_PLOT_LEFT,
  TIMELINE_PLOT_RIGHT,
  TIMELINE_PLOT_TOP,
  TimelineMarkerClick,
  anomalyDrilldownWindow,
  buildAreaPath,
  buildLinePath,
  formatUtcClock,
  timelineXScale,
  timelineYScale,
} from './svg-timeline.component';

// ── Fixtures — naive-UTC wire strings (D10) ─────────────────────────

function point(t: string, overrides: Partial<HistoryPoint> = {}): HistoryPoint {
  return {
    t,
    daemon_cpu: 2,
    daemon_rss_mb: 1700,
    cgroup_mem_mb: 5800,
    threads: 40,
    instances_active: 5,
    ...overrides,
  };
}

const POINTS: HistoryPoint[] = [
  point('2026-10-18T16:00:00', { daemon_rss_mb: 1700, daemon_cpu: 1, threads: 40 }),
  point('2026-10-18T16:15:00', { daemon_rss_mb: 1750, daemon_cpu: 2, threads: 42 }),
  point('2026-10-18T16:30:00', { daemon_rss_mb: 1800, daemon_cpu: 3, threads: 44 }),
  point('2026-10-18T16:45:00', { daemon_rss_mb: 1850, daemon_cpu: 2, threads: 44 }),
  point('2026-10-18T17:00:00', { daemon_rss_mb: 1900, daemon_cpu: 4, threads: 46 }),
];

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

const VIEWBOX_WIDTH = 1000;
const EXPECTED_PLOT_WIDTH = TIMELINE_PLOT_RIGHT - TIMELINE_PLOT_LEFT;

// ── Pure helper tests ───────────────────────────────────────────────

describe('svg-timeline pure helpers', () => {
  it('timelineXScale maps domain endpoints to plot edges and the midpoint to centre', () => {
    const t0 = Date.UTC(2026, 9, 18, 16, 0, 0);
    const t1 = Date.UTC(2026, 9, 18, 17, 0, 0);

    expect(timelineXScale(t0, t0, t1)).toBeCloseTo(TIMELINE_PLOT_LEFT, 6);
    expect(timelineXScale(t1, t0, t1)).toBeCloseTo(TIMELINE_PLOT_RIGHT, 6);
    expect(timelineXScale((t0 + t1) / 2, t0, t1)).toBeCloseTo(
      TIMELINE_PLOT_LEFT + EXPECTED_PLOT_WIDTH / 2,
      6,
    );
  });

  it('timelineXScale degenerates to the midpoint when the domain has no span', () => {
    const t = Date.UTC(2026, 9, 18, 16, 0, 0);
    expect(timelineXScale(t, t, t)).toBeCloseTo(
      TIMELINE_PLOT_LEFT + EXPECTED_PLOT_WIDTH / 2,
      6,
    );
  });

  it('timelineYScale maps min to the floor and max to the ceiling', () => {
    expect(timelineYScale(100, 100, 200)).toBeCloseTo(TIMELINE_PLOT_BOTTOM, 6);
    expect(timelineYScale(200, 100, 200)).toBeCloseTo(TIMELINE_PLOT_TOP, 6);
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

  it('anomalyDrilldownWindow pads by max(60s, 10% of the anomaly span)', () => {
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

  it('formatUtcClock formats in UTC regardless of the process timezone', () => {
    const ms = Date.UTC(2026, 9, 18, 23, 45, 0);
    expect(formatUtcClock(ms)).toBe('23:45');
  });
});

// ── Component render tests ──────────────────────────────────────────

describe('SvgTimelineComponent', () => {
  let fixture: ComponentFixture<SvgTimelineComponent>;
  let component: SvgTimelineComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SvgTimelineComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(SvgTimelineComponent);
    component = fixture.componentInstance;
  });

  it('renders a non-empty line + area per default-enabled series', () => {
    fixture.componentRef.setInput('points', POINTS);
    fixture.detectChanges();

    const lines = fixture.debugElement.queryAll(By.css('[data-test="series-line"]'));
    const areas = fixture.debugElement.queryAll(By.css('.series-area'));

    expect(lines.length).toBe(DEFAULT_TIMELINE_SERIES.length);
    expect(areas.length).toBe(DEFAULT_TIMELINE_SERIES.length);
    for (const line of lines) {
      expect((line.nativeElement.getAttribute('d') ?? '').length).toBeGreaterThan(0);
    }
    for (const area of areas) {
      expect((area.nativeElement.getAttribute('d') ?? '').length).toBeGreaterThan(0);
    }
  });

  it('renders only the series named by the series input', () => {
    fixture.componentRef.setInput('points', POINTS);
    fixture.componentRef.setInput('series', ['daemon_cpu']);
    fixture.detectChanges();

    const lines = fixture.debugElement.queryAll(By.css('[data-test="series-line"]'));
    expect(lines.length).toBe(1);
    expect(lines[0].nativeElement.getAttribute('data-series')).toBe('daemon_cpu');
  });

  it('positions anomaly markers by t using the naive-UTC parse', () => {
    fixture.componentRef.setInput('points', POINTS);
    // 16:30 is the midpoint of the 16:00–17:00 domain → plot centre.
    fixture.componentRef.setInput('anomalies', [anomaly()]);
    fixture.detectChanges();

    const dots = fixture.debugElement.queryAll(
      By.css('[data-test="anomaly-marker-dot"]'),
    );
    expect(dots.length).toBe(1);

    const cx = Number(dots[0].nativeElement.getAttribute('cx'));
    expect(cx).toBeCloseTo(TIMELINE_PLOT_LEFT + EXPECTED_PLOT_WIDTH / 2, 6);
  });

  it('renders UTC time-axis tick labels', () => {
    fixture.componentRef.setInput('points', POINTS);
    fixture.detectChanges();

    const ticks = fixture.debugElement.queryAll(By.css('[data-test="timeline-tick"]'));
    expect(ticks.map((t) => t.nativeElement.textContent.trim())).toEqual([
      '16:00',
      '16:15',
      '16:30',
      '16:45',
      '17:00',
    ]);
  });

  it('emits {anomaly, t0, t1} on marker click with the padded window', () => {
    fixture.componentRef.setInput('points', POINTS);
    fixture.componentRef.setInput('anomalies', [anomaly()]);
    fixture.detectChanges();

    const emitted: TimelineMarkerClick[] = [];
    component.markerClick.subscribe((event) => emitted.push(event));

    const marker = fixture.debugElement.query(By.css('[data-test="anomaly-marker"]'));
    marker.nativeElement.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(emitted.length).toBe(1);
    expect(emitted[0].anomaly.id).toBe('anom-1');
    expect(emitted[0].t0).toBe('2026-10-18T16:29:00.000Z');
    expect(emitted[0].t1).toBe('2026-10-18T16:41:00.000Z');
  });

  it('shows the empty label when there are no points or anomalies', () => {
    fixture.detectChanges();
    const empty = fixture.debugElement.query(By.css('[data-test="timeline-empty"]'));
    expect(empty).not.toBeNull();
  });

  it('keeps the viewBox fixed (never distorted by preserveAspectRatio=none)', () => {
    fixture.detectChanges();
    const svg = fixture.debugElement.query(By.css('[data-test="svg-timeline"]'));
    expect(svg.nativeElement.getAttribute('viewBox')).toBe(
      `0 0 ${VIEWBOX_WIDTH} 320`,
    );
    expect(svg.nativeElement.getAttribute('preserveAspectRatio')).toBe(
      'xMidYMid meet',
    );
  });
});
