// The CHARTS TAB spec (P5 task 5.3/5.4/5.5/5.8 · D1 pack `metrics-fe-page`).
//
// The load-bearing pins:
//
//   * the window request — `window_s=3600` ⇒ 240 points at the door's
//     `max_points` default of 240;
//   * the STEP/LINE rendering decision — a COUNTER renders STEP, a GAUGE
//     renders LINEAR (approver B5: a linear interpolation between
//     counter samples would assert values that were never observed);
//   * the visibility gate — the tab does not poll until the catalog has
//     named the chart-primary series (an empty name list would ask the
//     door for EVERY series).

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Observable, Subject } from 'rxjs';

import { MetricsService } from '../../../services/metrics.service';
import { MetricsSeriesPayload } from '../../../models/metrics.model';
import { ChartsTabComponent, MetricsChartSeriesSpec } from './charts-tab.component';

function envelope() {
  return {
    schema_version: 1,
    vocabulary_version: 2,
    generated_at_utc: '2026-11-02T09:00:00+00:00',
    restart_epoch: 'abc123',
    activation: {
      listener_enabled: false,
      census_enabled: true,
      query_api: 'auth-gated-always-on',
    },
  };
}

interface SeriesCall {
  readonly options: { names?: readonly string[]; windowS?: number; maxPoints?: number };
  readonly subject: Subject<MetricsSeriesPayload>;
}

class StubMetricsService {
  readonly seriesCalls: SeriesCall[] = [];

  getSeries(options: SeriesCall['options'] = {}): Observable<MetricsSeriesPayload> {
    const subject = new Subject<MetricsSeriesPayload>();
    this.seriesCalls.push({ options, subject });
    return subject.asObservable();
  }
}

const SPECS: readonly MetricsChartSeriesSpec[] = [
  { name: 'ensemble_db_pool_requests_errors_total', chartRole: 'step' },
  { name: 'ensemble_loop_lag_seconds', chartRole: 'line' },
];

function trendPayload(): MetricsSeriesPayload {
  return {
    ...envelope(),
    window: {
      from_utc: '2026-11-02T08:00:00+00:00',
      to_utc: '2026-11-02T09:00:00+00:00',
      interval_s: 15,
      interval_s_source: 'observed',
    },
    last_sample_age_s: 2,
    stale: false,
    truncated: false,
    series: [
      {
        name: 'ensemble_db_pool_requests_errors_total',
        labels: {},
        kind: 'counter',
        monotonic: true,
        points: [
          [1_700_000_000, 4],
          [1_700_000_015, 9],
        ],
      },
      {
        name: 'ensemble_loop_lag_seconds',
        labels: { stat: 'p50' },
        kind: 'gauge',
        monotonic: false,
        points: [
          [1_700_000_000, 0.1],
          [1_700_000_015, 0.4],
        ],
      },
    ],
  };
}

describe('ChartsTabComponent — the request + the rendering mode', () => {
  let fixture: ComponentFixture<ChartsTabComponent>;
  let component: ChartsTabComponent;
  let service: StubMetricsService;

  beforeEach(async () => {
    jest.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [ChartsTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    service = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(ChartsTabComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('chartSpecs', SPECS);
  });

  afterEach(() => {
    fixture.destroy();
    jest.useRealTimers();
  });

  it('declares the named poll interval (15 s) and the 3600 s window', () => {
    expect(component.POLL_INTERVAL_MS).toBe(15_000);
    expect(component.WINDOW_S).toBe(3600);
  });

  it('does NOT poll until the catalog names the chart-primary series', () => {
    fixture.componentRef.setInput('chartSpecs', []);
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    expect(service.seriesCalls).toHaveLength(0);
  });

  it('requests the chart-primary names over a 3600 s window', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    expect(service.seriesCalls).toHaveLength(1);
    expect(service.seriesCalls[0].options.windowS).toBe(3600);
    expect(service.seriesCalls[0].options.names).toEqual([
      'ensemble_db_pool_requests_errors_total',
      'ensemble_loop_lag_seconds',
    ]);
  });

  it('renders a COUNTER as STEP and a GAUGE as LINEAR', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.seriesCalls[0].subject.next(trendPayload());

    const charts = component.charts();
    expect(charts).toHaveLength(2);
    const counter = charts.find((chart) =>
      chart.id.includes('ensemble_db_pool_requests_errors_total'),
    );
    const gauge = charts.find((chart) => chart.id.includes('ensemble_loop_lag_seconds'));
    expect(counter?.mode).toBe('step');
    expect(gauge?.mode).toBe('line');
  });

  it('falls back to the KIND-derived mode when the catalog names no mode', () => {
    // The catalog names only the gauge — the counter's mode is derived
    // from its kind, exactly as the door derives it.
    fixture.componentRef.setInput('chartSpecs', [
      { name: 'ensemble_loop_lag_seconds', chartRole: 'line' },
    ]);
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.seriesCalls[0].subject.next(trendPayload());
    const counter = component.charts().find((chart) => chart.id.includes('errors_total'));
    expect(counter?.mode).toBe('step');
  });

  it('converts the trend timestamps from epoch SECONDS to milliseconds', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.seriesCalls[0].subject.next(trendPayload());
    const counter = component.charts().find((chart) => chart.id.includes('errors_total'));
    expect(counter?.points[0].t).toBe(1_700_000_000 * 1000);
  });

  it('marks the view STALE when the door reports a stale trend', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.seriesCalls[0].subject.next({ ...trendPayload(), stale: true });
    expect(component.stale()).toBe(true);
  });

  it('surfaces a bounded series page — the W-2 two-axis bound is never silent', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.seriesCalls[0].subject.next({
      ...trendPayload(),
      truncated: true,
      next_cursor: 'opaque',
    });
    fixture.detectChanges();

    const meta = fixture.nativeElement.querySelector('[data-test="charts-meta"]');
    expect(meta).not.toBeNull();
    const text = (meta.textContent ?? '').toLowerCase();
    expect(text).toContain('bounded');
    // The returned series still render (never blank, never wrong).
    expect(component.charts()).toHaveLength(2);
  });

  it('renders no bound note when the series page is complete', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.seriesCalls[0].subject.next(trendPayload());
    fixture.detectChanges();

    const text = (
      fixture.nativeElement.querySelector('[data-test="charts-meta"]')?.textContent ?? ''
    ).toLowerCase();
    expect(text).not.toContain('bounded');
  });
});
