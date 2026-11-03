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

/**
 * A series payload over the given window, with evenly spaced points.
 *
 * The door's `interval_s` is the OBSERVED cadence (15 s); the points are
 * the ones actually on screen — the meta line must report the spacing
 * DERIVED from these.
 */
function seriesPayload(
  fromUtc: string,
  toUtc: string,
  points = 4,
): MetricsSeriesPayload {
  const fromMs = Date.parse(fromUtc);
  const toMs = Date.parse(toUtc);
  const step = Math.max(1, Math.floor((toMs - fromMs) / 1000 / points));
  const samples: [number, number][] = [];
  for (let index = 0; index < points; index += 1) {
    samples.push([Math.floor(fromMs / 1000) + index * step, index]);
  }
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
      from_utc: fromUtc,
      to_utc: toUtc,
      interval_s: 15,
      interval_s_source: 'observed',
    },
    last_sample_age_s: 1,
    stale: false,
    series: [
      {
        name: 'ensemble_readiness_component_state',
        labels: {},
        kind: 'gauge',
        monotonic: false,
        points: samples,
      },
    ],
    truncated: false,
  } as MetricsSeriesPayload;
}

class StubMetricsService {
  readonly families$ = new Subject<MetricsFamiliesPayload>();
  readonly snapshot$ = new Subject<MetricsSnapshotPayload>();
  readonly series$ = new Subject<MetricsSeriesPayload>();
  /**
   * ONE subject PER REQUEST.
   *
   * `series$.next(...)` reaches EVERY request (the historical channel);
   * `seriesSubjects[i].next(...)` reaches exactly ONE — which is how a
   * spec answers a request that has been superseded (3.10's
   * old-generation response).
   */
  readonly seriesSubjects: Subject<MetricsSeriesPayload>[] = [];
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
    const subject = new Subject<MetricsSeriesPayload>();
    this.seriesSubjects.push(subject);
    this.series$.subscribe((value) => subject.next(value));
    return subject.asObservable();
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

// ── THE PHASE-3 RANGE + BASELINE (3.3 / 3.4 / 3.5 / 3.10 / 3.11) ────────
//
// The load-bearing pins:
//
//   * the frame's window reaches the door as `window_s` (the tab body
//     never hard-codes a window);
//   * the baseline SHARES the tab's single series request — 2W over ONE
//     request, never a second request per family;
//   * a window change issues EXACTLY ONE series request;
//   * an in-flight response from the PREVIOUS generation is discarded
//     (the old-shape payload never paints under the new label);
//   * a CLAMPED payload publishes NO baseline and says why;
//   * the request NEVER carries a window above the substrate ceiling;
//   * the meta line prints the DERIVED spacing, not the door's cadence.
describe('MetricsCentralTabComponent — the range → request plumbing (3.3 / 3.10)', () => {
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
    fixture.componentRef.setInput('state', state({ families: [family({ id: 'F9' })] }));
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('the frame\'s window reaches the door as `window_s`', () => {
    fixture.componentRef.setInput('window', '30m');
    fixture.detectChanges();
    expect(stub.seriesRequests.length).toBe(1);
    expect(stub.seriesRequests[0].windowS).toBe(1800);
  });

  it('a window change issues EXACTLY ONE series request — not two, not zero', () => {
    fixture.detectChanges();
    expect(stub.seriesRequests.length).toBe(1);
    fixture.componentRef.setInput('window', '15m');
    fixture.detectChanges();
    expect(stub.seriesRequests.length).toBe(2);
    expect(stub.seriesRequests[1].windowS).toBe(900);
  });

  it('the baseline SHARES the request — 2W over ONE request, never a second one (3.4 / 3.10)', () => {
    fixture.componentRef.setInput('window', '1h');
    fixture.componentRef.setInput('baseline', true);
    fixture.detectChanges();
    expect(stub.seriesRequests.length).toBe(1);
    expect(stub.seriesRequests[0].windowS).toBe(7200);
  });

  it('the request NEVER carries a window above the substrate ceiling (3.5b)', () => {
    fixture.componentRef.setInput('window', '2h');
    fixture.componentRef.setInput('baseline', true);
    fixture.detectChanges();
    for (const request of stub.seriesRequests) {
      expect(request.windowS ?? 0).toBeLessThanOrEqual(7200);
    }
  });

  it('an in-flight response from the PREVIOUS generation is DISCARDED (3.10)', () => {
    const patches: Partial<MetricsTabState>[] = [];
    fixture.componentRef.setInput('onState', (patch: Partial<MetricsTabState>) => {
      patches.push(patch);
    });
    fixture.detectChanges();
    expect(stub.seriesRequests.length).toBe(1);
    // The window moves while the first request is still in flight.
    fixture.componentRef.setInput('window', '15m');
    fixture.detectChanges();
    expect(stub.seriesRequests.length).toBe(2);
    // The OLD response lands now — it is old-shape and must be dropped.
    stub.seriesSubjects[0].next(
      seriesPayload('2026-11-03T00:00:00+00:00', '2026-11-03T01:00:00+00:00'),
    );
    fixture.detectChanges();
    expect(patches.filter((patch) => patch.doorState === 'ok')).toHaveLength(0);
  });

  it('the CURRENT generation\'s response IS published', () => {
    const patches: Partial<MetricsTabState>[] = [];
    fixture.componentRef.setInput('onState', (patch: Partial<MetricsTabState>) => {
      patches.push(patch);
    });
    fixture.detectChanges();
    stub.series$.next(seriesPayload('2026-11-03T00:00:00+00:00', '2026-11-03T01:00:00+00:00'));
    fixture.detectChanges();
    expect(patches.filter((patch) => patch.doorState === 'ok')).toHaveLength(1);
  });
});

describe('MetricsCentralTabComponent — the baseline split + the refusal (3.4 / 3.5c)', () => {
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
    fixture.componentRef.setInput('state', state({ families: [family({ id: 'F9' })] }));
    fixture.componentRef.setInput('onState', (patch: Partial<MetricsTabState>) => {
      patches.push(patch);
    });
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('publishes the CURRENT half and the PREVIOUS half of the 2W payload', () => {
    fixture.componentRef.setInput('window', '1h');
    fixture.componentRef.setInput('baseline', true);
    fixture.detectChanges();
    stub.series$.next(seriesPayload('2026-11-03T00:00:00+00:00', '2026-11-03T02:00:00+00:00'));
    fixture.detectChanges();
    const published = patches[patches.length - 1];
    expect(published.baselineSeries?.[0].points.length).toBe(2);
    expect(published.series?.[0].points.length).toBe(2);
    expect(published.baselineRefusal).toBeNull();
  });

  it('a CLAMPED payload publishes NO baseline and says why (3.5c)', () => {
    fixture.componentRef.setInput('window', '1h');
    fixture.componentRef.setInput('baseline', true);
    fixture.detectChanges();
    // The door clamped the 2 h request to 1 h and answered 200.
    stub.series$.next(seriesPayload('2026-11-03T00:00:00+00:00', '2026-11-03T01:00:00+00:00'));
    fixture.detectChanges();
    const published = patches[patches.length - 1];
    expect(published.baselineSeries).toEqual([]);
    expect(published.baselineRefusal).toContain('clamped');
  });

  it('the refusal is RENDERED next to the panels', () => {
    fixture.componentRef.setInput('state', state({
      families: [family({ id: 'F9' })],
      baselineRefusal: 'the door clamped the window — the previous window is unavailable, so no baseline is shown',
    }));
    fixture.detectChanges();
    const refusal = fixture.nativeElement.querySelector('[data-test="central-tab-baseline-refused"]');
    expect(refusal).not.toBeNull();
    expect((refusal.textContent ?? '')).toContain('clamped');
  });
});

describe('MetricsCentralTabComponent — the effective-interval meta (3.11 / AM-18)', () => {
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

  it('prints the DERIVED spacing at the 2 h option — NOT the door\'s observed cadence', () => {
    // 2 h over 240 points ⇒ 30 s apart, while the door says 15 s.
    fixture.componentRef.setInput('state', state({
      families: [family({ id: 'F9' })],
      lastGood: seriesPayload(
        '2026-11-03T00:00:00+00:00',
        '2026-11-03T02:00:00+00:00',
        240,
      ),
    }));
    fixture.detectChanges();
    const meta = fixture.nativeElement.querySelector('[data-test="central-tab-meta"]');
    expect(meta).not.toBeNull();
    const text = meta.textContent ?? '';
    expect(text).toContain('30s');
    expect(text).not.toContain('15s');
  });

  it('says so when there are no points yet', () => {
    fixture.detectChanges();
    const meta = fixture.nativeElement.querySelector('[data-test="central-tab-meta"]');
    expect((meta.textContent ?? '')).toContain('no points yet');
  });
});
