// The CENTRAL TAB BODY spec (T1 phase 2, task 2.4/2.11).
//
// The load-bearing pins:
//
//   * the CHART-PRIMARY PROJECTION — the three assertions the shell
//     spec used to carry, RE-POINTED here (the projection moved from
//     the frame to the tab body: each tab projects ITS OWN bound
//     families);
//   * ADJ-vi — a bound family is charted ONLY while it is live; a dark
//     family is LISTED with the reason the door gave, never charted and
//     never silently dropped;
//   * the selector ceiling — the FE's own slice is surfaced, never
//     silent;
//   * the stat strip — the tab asks the door for exactly its stat-role
//     families;
//   * the per-source reporting — the body reports its OWN source's
//     outcome and never writes another tab's state.

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Subject } from 'rxjs';

import { MetricsService } from '../../../services/metrics.service';
import {
  MetricsFamiliesPayload,
  MetricsFamily,
  MetricsSeriesPayload,
  MetricsSnapshotPayload,
} from '../../../models/metrics.model';
import { METRICS_SELECTOR_CEILING } from '../metrics-door';
import { MetricsTabState } from '../metrics-state';
import { METRICS_CENTRAL_ENTRIES, metricsCentralTab } from '../tabs.manifest';
import { MetricsCentralTabComponent } from './central-tab.component';

function family(overrides: Partial<MetricsFamily> = {}): MetricsFamily {
  return {
    id: 'F9',
    names: [
      {
        name: 'ensemble_readiness_component_state',
        kind: 'gauge',
        labels: null,
        help: '',
        buckets: null,
        cardinality_class: 'closed',
        monotonic: false,
        chart_role: 'line',
      },
    ],
    live_series_count: 1,
    availability: { state: 'live' },
    role: 'chart',
    source_idiom: 'registry',
    durability: 'process',
    cardinality_contribution: 1,
    ...overrides,
  };
}

function state(overrides: Partial<MetricsTabState> = {}): MetricsTabState {
  return {
    doorState: 'ok',
    families: [],
    series: [],
    lastGood: null,
    errorClass: null,
    ...overrides,
  };
}

class StubMetricsService {
  readonly families$ = new Subject<MetricsFamiliesPayload>();
  readonly snapshot$ = new Subject<MetricsSnapshotPayload>();
  readonly series$ = new Subject<MetricsSeriesPayload>();
  readonly seriesRequests: { names?: readonly string[]; windowS?: number }[] = [];
  readonly snapshotRequests: { family?: string }[] = [];

  getFamilies() {
    return this.families$.asObservable();
  }
  getSnapshot(options: { family?: string } = {}) {
    this.snapshotRequests.push(options);
    return this.snapshot$.asObservable();
  }
  getSeries(options: { names?: readonly string[]; windowS?: number } = {}) {
    this.seriesRequests.push(options);
    return this.series$.asObservable();
  }
}

describe('MetricsCentralTabComponent — the chart-primary projection (re-pointed from the shell)', () => {
  let fixture: ComponentFixture<MetricsCentralTabComponent>;
  let component: MetricsCentralTabComponent;
  let stub: StubMetricsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsCentralTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    stub = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(MetricsCentralTabComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('entry', metricsCentralTab('runtime'));
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('projects ONLY the chart-primary families of the LIVE bound families', () => {
    fixture.componentRef.setInput('state', state({
      families: [
        family({ id: 'F9' }),
        family({
          id: 'F3',
          role: 'stat',
          names: [
            {
              name: 'ensemble_wedge_capture_total',
              kind: 'counter',
              labels: null,
              help: '',
              buckets: null,
              cardinality_class: 'open',
              monotonic: true,
              chart_role: 'step',
            },
          ],
        }),
      ],
    }));
    fixture.detectChanges();
    // F3 is stat-primary — it feeds the stat strip, not the trend.
    expect(component.chartSpecs().map((spec) => spec.name)).toEqual([
      'ensemble_readiness_component_state',
    ]);
  });

  it('carries the rendering mode of each series (a counter renders STEP)', () => {
    fixture.componentRef.setInput('state', state({
      families: [
        family({
          id: 'F4',
          names: [
            {
              name: 'ensemble_db_pool_requests_errors_total',
              kind: 'counter',
              labels: null,
              help: '',
              buckets: null,
              cardinality_class: 'open',
              monotonic: true,
              chart_role: 'step',
            },
          ],
        }),
      ],
    }));
    fixture.detectChanges();
    expect(component.chartSpecs()[0].chartRole).toBe('step');
  });

  it('caps the selector list at the door\'s ceiling (an over-ceiling request is rejected)', () => {
    const names = Array.from({ length: 80 }, (_unused, index) => ({
      name: `metric_${index}`,
      kind: 'gauge' as const,
      labels: null,
      help: '',
      buckets: null,
      cardinality_class: 'closed' as const,
      monotonic: false,
      chart_role: 'line' as const,
    }));
    fixture.componentRef.setInput('state', state({ families: [family({ id: 'F1', names })] }));
    fixture.detectChanges();
    expect(component.chartSpecs().length).toBeLessThanOrEqual(METRICS_SELECTOR_CEILING);
    // The slice is SURFACED, never silent.
    expect(component.selectorSliced()).toBe(true);
  });
});

describe('MetricsCentralTabComponent — the availability rendering (ADJ-vi)', () => {
  let fixture: ComponentFixture<MetricsCentralTabComponent>;
  let component: MetricsCentralTabComponent;
  let stub: StubMetricsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsCentralTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    stub = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(MetricsCentralTabComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('entry', metricsCentralTab('runtime'));
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('renders a LIVE family as a chartable panel', () => {
    fixture.componentRef.setInput('state', state({ families: [family({ id: 'F9' })] }));
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('[data-test="central-tab-family-live"]'),
    ).not.toBeNull();
    expect(
      fixture.nativeElement.querySelector('[data-test="central-tab-family-dark"]'),
    ).toBeNull();
  });

  it('renders a DARK family with its reason, and never charts it (ADJ-vi)', () => {
    fixture.componentRef.setInput('state', state({
      families: [
        family({
          id: 'F1',
          live_series_count: 0,
          availability: { state: 'dark', reason: 'capture_off' },
        }),
      ],
    }));
    fixture.detectChanges();
    const dark = fixture.nativeElement.querySelector('[data-test="central-tab-family-dark"]');
    expect(dark).not.toBeNull();
    expect(dark.getAttribute('data-reason')).toBe('capture_off');
    expect((dark.textContent ?? '')).toContain('the wedge capture is not active');
    // The dark family's names are NOT requested from the door.
    expect(stub.seriesRequests.length).toBe(0);
  });

  it('requests ONLY the LIVE families\' series, with a window', () => {
    fixture.componentRef.setInput('state', state({
      families: [
        family({ id: 'F9' }),
        family({
          id: 'F1',
          live_series_count: 0,
          availability: { state: 'dark', reason: 'capture_off' },
        }),
      ],
    }));
    fixture.detectChanges();
    expect(stub.seriesRequests.length).toBe(1);
    expect(stub.seriesRequests[0].names).toEqual(['ensemble_readiness_component_state']);
    expect(stub.seriesRequests[0].windowS).toBeGreaterThan(0);
  });

  it('asks the door for exactly its stat-role families (the stat strip)', () => {
    fixture.componentRef.setInput('state', state({ families: [family({ id: 'F3' })] }));
    fixture.detectChanges();
    expect(stub.snapshotRequests.map((request) => request.family)).toEqual(['F3']);
  });

  it('the tab is NEVER an empty shell — it names its binding while the catalog loads', () => {
    fixture.detectChanges();
    const hint = fixture.nativeElement.querySelector('[data-test="central-tab-no-catalog"]');
    expect(hint).not.toBeNull();
    expect((hint.textContent ?? '')).toContain('F9');
  });
});

describe('MetricsCentralTabComponent — EVERY central tab (the W-10 exit criterion)', () => {
  let fixture: ComponentFixture<MetricsCentralTabComponent>;
  let component: MetricsCentralTabComponent;
  let stub: StubMetricsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsCentralTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    stub = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(MetricsCentralTabComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('the manifest declares SIX central tabs', () => {
    expect(METRICS_CENTRAL_ENTRIES).toHaveLength(6);
  });

  for (const entry of METRICS_CENTRAL_ENTRIES) {
    it(`the ${entry.id} tab fetches ITS bound families and surfaces a dark family's reason`, () => {
      fixture.componentRef.setInput('entry', entry);

      // One bound family is LIVE, one is DARK (the false-dark case).
      const [liveId, darkId] = [entry.familyIds[0], entry.familyIds[1] ?? entry.familyIds[0]];
      fixture.componentRef.setInput('state', state({
        families: [
          family({
            id: liveId,
            names: [
              {
                name: `ensemble_${entry.id}_live`,
                kind: 'gauge',
                labels: null,
                help: '',
                buckets: null,
                cardinality_class: 'closed',
                monotonic: false,
                chart_role: 'line',
              },
            ],
          }),
          family({
            id: darkId,
            live_series_count: 0,
            availability: { state: 'dark', reason: 'capture_off' },
            names: [
              {
                name: `ensemble_${entry.id}_dark`,
                kind: 'gauge',
                labels: null,
                help: '',
                buckets: null,
                cardinality_class: 'closed',
                monotonic: false,
                chart_role: 'line',
              },
            ],
          }),
        ],
      }));
      fixture.detectChanges();

      // The LIVE family's series are requested from the door.
      expect(stub.seriesRequests.length).toBe(1);
      expect(stub.seriesRequests[0].names).toEqual([`ensemble_${entry.id}_live`]);

      // The DARK family is LISTED with its reason — never charted, never dropped.
      const dark = fixture.nativeElement.querySelector(
        '[data-test="central-tab-family-dark"]',
      ) as HTMLElement;
      expect(dark).not.toBeNull();
      expect(dark.getAttribute('data-reason')).toBe('capture_off');
      expect((dark.textContent ?? '')).toContain('the wedge capture is not active');

      // The tab is never an empty shell — it renders its binding.
      expect(
        fixture.nativeElement.querySelector('[data-test="central-tab-families"]'),
      ).not.toBeNull();
    });
  }
});

describe('MetricsCentralTabComponent — the per-source reporting (2.11)', () => {
  let fixture: ComponentFixture<MetricsCentralTabComponent>;
  let component: MetricsCentralTabComponent;
  let stub: StubMetricsService;
  let patches: Partial<MetricsTabState>[];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsCentralTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    stub = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(MetricsCentralTabComponent);
    component = fixture.componentInstance;
    patches = [];
    fixture.componentRef.setInput('entry', metricsCentralTab('runtime'));
    fixture.componentRef.setInput('onState', (patch: Partial<MetricsTabState>) => {
      patches.push(patch);
    });
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('reports its own source\'s outcome to the frame', () => {
    fixture.componentRef.setInput('state', state({ families: [family({ id: 'F9' })] }));
    fixture.detectChanges();
    stub.series$.next({
      schema_version: 1,
      vocabulary_version: 2,
      generated_at_utc: '2026-11-03T00:00:00+00:00',
      restart_epoch: 'abc',
      activation: {
        listener_enabled: true,
        census_enabled: true,
        query_api: 'auth-gated-always-on',
      },
      window: {
        from_utc: '2026-11-03T00:00:00+00:00',
        to_utc: '2026-11-03T01:00:00+00:00',
        interval_s: 15,
        interval_s_source: 'observed',
      },
      last_sample_age_s: 1,
      stale: false,
      series: [],
      truncated: false,
    } as MetricsSeriesPayload);
    fixture.detectChanges();
    expect(patches.length).toBeGreaterThan(0);
    expect(patches[patches.length - 1].doorState).toBe('ok');
    expect(patches[patches.length - 1].errorClass).toBeNull();
  });

  it('the timeout class renders the door copy and NO Retry (the read may still be running)', () => {
    fixture.componentRef.setInput('state', state({ errorClass: 'timeout' }));
    fixture.detectChanges();
    const banner = fixture.nativeElement.querySelector('[data-test="central-tab-error"]');
    expect(banner).not.toBeNull();
    expect((banner.textContent ?? '')).toContain('did not answer in time');
    expect(fixture.nativeElement.querySelector('[data-test="central-tab-retry"]')).toBeNull();
  });

  it('a retryable failure DOES offer the Retry affordance', () => {
    fixture.componentRef.setInput('state', state({ errorClass: 'auth_not_configured' }));
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('[data-test="central-tab-retry"]'),
    ).not.toBeNull();
  });
});
