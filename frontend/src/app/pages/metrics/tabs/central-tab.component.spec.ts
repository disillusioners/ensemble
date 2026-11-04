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

import { readFileSync } from 'fs';
import { join } from 'path';

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

  // ── THE PHASE-8 HONESTY LEGS (tasks 8.7 / 8.8) ─────────────────────

  it('a tab whose families are ALL dark renders EVERY family reason (8.7)', () => {
    // The "enabled-but-empty" defect the audit named: a tab that is
    // enabled, binds families, and shows NOTHING. A tab whose families
    // are all dark shows each family's OWN reason (ADJ-vi).
    fixture.componentRef.setInput('state', state({
      families: [
        family({
          id: 'F1',
          live_series_count: 0,
          availability: { state: 'dark', reason: 'capture_off' },
        }),
        family({
          id: 'F2',
          live_series_count: 0,
          availability: { state: 'dark', reason: 'census_gated' },
        }),
      ],
    }));
    fixture.detectChanges();
    const darks = fixture.nativeElement.querySelectorAll('[data-test="central-tab-family-dark"]');
    expect(darks.length).toBe(2);
    const reasons = Array.from(darks).map((node) => node.getAttribute('data-reason'));
    expect(reasons).toEqual(['capture_off', 'census_gated']);
    // The reasons are rendered as TEXT, not only as an attribute.
    expect(fixture.nativeElement.textContent ?? '').toContain('the wedge capture is not active');
  });

  it('a DARK family does NOT consume the selector ceiling (8.8 / ADJ-vi)', () => {
    // The FE slices the LIVE series at the ceiling. A dark family has
    // no series to plot, so it must not eat a slot — otherwise the live
    // families that follow are cut SILENTLY.
    const liveNames = Array.from({ length: 60 }, (_unused, index) => ({
      name: `live_metric_${index}`,
      kind: 'gauge' as const,
      labels: null,
      help: '',
      buckets: null,
      cardinality_class: 'closed' as const,
      monotonic: false,
      chart_role: 'line' as const,
    }));
    const darkNames = Array.from({ length: 10 }, (_unused, index) => ({
      name: `dark_metric_${index}`,
      kind: 'gauge' as const,
      labels: null,
      help: '',
      buckets: null,
      cardinality_class: 'closed' as const,
      monotonic: false,
      chart_role: 'line' as const,
    }));
    fixture.componentRef.setInput('state', state({
      families: [
        family({ id: 'F1', names: liveNames }),
        family({
          id: 'F2',
          names: darkNames,
          live_series_count: 0,
          availability: { state: 'dark', reason: 'capture_off' },
        }),
      ],
    }));
    fixture.detectChanges();
    // The slice is FULL of the LIVE family's series — the dark family
    // contributed nothing.
    expect(component.chartSpecs().length).toBe(METRICS_SELECTOR_CEILING);
    expect(component.chartSpecs().every((spec) => spec.familyId === 'F1')).toBe(true);
    // And the dropped count counts the LIVE series only.
    expect(component.droppedSeriesCount()).toBe(10);
  });

  it('the FE\'s own truncation is SURFACED with a count and no phantom page (8.8)', () => {
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
    const hint = fixture.nativeElement.querySelector('[data-test="central-tab-selector-slice"]');
    expect(hint).not.toBeNull();
    // The COUNT is stated.
    expect(hint?.textContent ?? '').toContain('30 series are not shown');
    // The copy names this page's OWN slice — and NEVER promises a
    // "next page" that does not exist (the FE drops them).
    expect(hint?.textContent ?? '').toContain("this page's own selector slice");
    expect(hint?.textContent ?? '').not.toContain('next page');
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
    // ── RE-POINTED BY PHASE 8 (task 8.2 / AM-4) ──────────────────────
    // This leg used to use `auth_not_configured` as its "retryable"
    // example. That was the DEFECT the plan names: a fail-closed CONFIG
    // error is not transient, so a Retry on it is an affordance that can
    // never succeed. The leg keeps its intent — a retryable failure
    // offers the Retry — and now names a class that IS retryable.
    fixture.componentRef.setInput('state', state({ errorClass: 'network' }));
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('[data-test="central-tab-retry"]'),
    ).not.toBeNull();
  });

  it('the AUTH state offers NO Retry (a config error is not transient — 8.2)', () => {
    // The PLANTED VARIANT the plan names: a Retry on the auth state
    // FAILS this leg. The actionable state carries the runbook pointer
    // instead of a button that can never succeed.
    fixture.componentRef.setInput('state', state({ errorClass: 'auth_not_configured' }));
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('[data-test="central-tab-retry"]'),
    ).toBeNull();
    const panel = fixture.nativeElement.querySelector('[data-test="central-tab-error"]');
    expect(panel).not.toBeNull();
    expect(panel?.textContent ?? '').toContain('docs/runbooks/oauth2-auth.md');
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

describe('MetricsCentralTabComponent — the DATA-END marker (T1 phase 8, task 8.10)', () => {
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

  it('the panel carries WHERE ITS DATA ENDS (the last plotted sample)', () => {
    const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);
    const T1 = Date.UTC(2026, 10, 2, 9, 1, 0);
    fixture.componentRef.setInput('state', state({
      families: [family({ id: 'F9' })],
      series: [
        {
          name: 'ensemble_readiness_component_state',
          labels: null,
          points: [
            [T0 / 1000, 1],
            [T1 / 1000, 2],
          ],
        },
      ],
    }));
    fixture.detectChanges();
    const groups = component.groupsOf('F9');
    expect(groups.length).toBe(1);
    const end = component.dataEndOf(groups[0]);
    expect(end).not.toBeNull();
    expect(end?.ms).toBe(T1);
    // The VISIBLE words ride the marker.
    expect(end?.label ?? '').toContain('data ends');
  });

  it('a panel with NO points carries NO data end (never a bogus marker)', () => {
    fixture.componentRef.setInput('state', state({ families: [family({ id: 'F9' })] }));
    fixture.detectChanges();
    const groups = component.groupsOf('F9');
    // No series → no group → nothing to mark.
    expect(groups.length).toBe(0);
  });

  it('the render data carries the data end (the renderer passes it to the panel)', () => {
    const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);
    fixture.componentRef.setInput('state', state({
      families: [family({ id: 'F9' })],
      series: [
        {
          name: 'ensemble_readiness_component_state',
          labels: null,
          points: [[T0 / 1000, 1]],
        },
      ],
    }));
    fixture.detectChanges();
    const group = component.groupsOf('F9')[0];
    expect(component.panelData(group).dataEnd?.ms).toBe(T0);
  });

  it('the STAT STRIP shows the sample age (the audit found no age on the cards)', () => {
    // The data-end marker is ON THE CHART; the age is ON THE CARD.
    // A stat family publishes a snapshot whose series carry
    // `last_publish_age_s` — the card must state it.
    // The first detectChanges arms the stat-strip request (the effect
    // subscribes on it) — the payload must arrive AFTER the
    // subscription, or it is dropped.
    fixture.detectChanges();
    stub.snapshot$.next({
      schema_version: 1,
      vocabulary_version: 2,
      generated_at_utc: '2026-11-04T00:00:00+00:00',
      restart_epoch: 'abc123',
      truncated: false,
      series: [
        {
          name: 'ensemble_db_pool_requests_errors_total',
          kind: 'counter',
          monotonic: true,
          value: 0,
          labels: null,
          last_publish_age_s: 42.5,
        },
      ],
    } as MetricsSnapshotPayload);
    fixture.detectChanges();
    const strip = fixture.nativeElement.querySelector('[data-test="central-tab-stat-strip"]');
    expect(strip).not.toBeNull();
    // The age is rendered — "published 42.5s ago".
    expect(strip.textContent ?? '').toContain('42.5');
    expect(strip.textContent ?? '').toContain('published');
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

// ═══════════════════════════════════════════════════════════════════════
// PHASE 4 (task 4.15) — the per-kind+unit PANEL COMPOSITION
// ═══════════════════════════════════════════════════════════════════════

describe('MetricsCentralTabComponent — the panel composition (4.15)', () => {
  let fixture: ComponentFixture<MetricsCentralTabComponent>;
  let component: MetricsCentralTabComponent;

  /**
   * A MIXED-KIND family — the wire registers a gauge AND a counter.
   *
   * This is the shape the plan names (F4 `w1.py:194-228`; F9
   * `w2.py:120-150`): a mixed-kind panel would break counter-honesty
   * and falsify the unit label, so the family must yield TWO panels.
   */
  const MIXED_KIND_FAMILY: MetricsFamily = {
    id: 'F4',
    names: [
      {
        name: 'ensemble_db_pool_checked_out',
        kind: 'gauge',
        labels: null,
        help: '',
        buckets: null,
        cardinality_class: 'closed',
        monotonic: false,
        chart_role: 'line',
      },
      {
        name: 'ensemble_db_pool_requests_errors_total',
        kind: 'counter',
        labels: null,
        help: '',
        buckets: null,
        cardinality_class: 'closed',
        monotonic: true,
        chart_role: 'step',
      },
    ],
    live_series_count: 2,
    availability: { state: 'live' },
    role: 'chart',
    source_idiom: 'registry',
    durability: 'process',
    cardinality_contribution: 1,
  };

  function mixedState(): MetricsTabState {
    return state({
      families: [MIXED_KIND_FAMILY],
      series: [
        {
          name: 'ensemble_db_pool_checked_out',
          labels: {},
          kind: 'gauge',
          monotonic: false,
          points: [
            [1_700_000_000, 3],
            [1_700_000_015, 5],
          ],
        },
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
      ],
    });
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsCentralTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsCentralTabComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('entry', metricsCentralTab('db-pool'));
  });

  afterEach(() => fixture.destroy());

  it('a MIXED-KIND family yields TWO panels — one per kind', () => {
    fixture.componentRef.setInput('state', mixedState());
    fixture.detectChanges();

    const panels = fixture.nativeElement.querySelectorAll(
      '[data-test="central-tab-panel"]',
    ) as NodeListOf<HTMLElement>;
    expect(panels.length).toBe(2);
    const kinds = Array.from(panels).map((panel) => panel.getAttribute('data-kind'));
    expect(kinds.sort()).toEqual(['line', 'step']);
  });

  it('the kind came from the WIRE (chart_role), never from an FE table', () => {
    fixture.componentRef.setInput('state', mixedState());
    fixture.detectChanges();

    const groups = component.groupsOf('F4');
    const counter = groups.find((group) =>
      group.series.some((series) => series.id === 'ensemble_db_pool_requests_errors_total'),
    );
    // The wire's `chart_role: 'step'` — the KIND (`counter`) alone would
    // have derived the same answer, so the pin is on the ROLE the wire
    // carried: a name whose kind says 'gauge' but whose chart_role says
    // 'step' must render STEP.
    expect(counter?.kind).toBe('step');
    expect(counter?.series.find((series) => series.id === 'ensemble_db_pool_requests_errors_total')?.mode).toBe(
      'step',
    );
  });

  it('renders THROUGH the registry (the resolved panel is mounted, not a hard-coded one)', () => {
    fixture.componentRef.setInput('state', mixedState());
    fixture.detectChanges();

    const proxies = fixture.nativeElement.querySelectorAll(
      '[data-test="chart-panel-proxy"]',
    ) as NodeListOf<HTMLElement>;
    expect(proxies.length).toBe(2);
    // The panel's own render modes survive the composition.
    const modes = Array.from(proxies).map((proxy) => proxy.getAttribute('data-render-mode'));
    expect(modes.sort()).toEqual(['line', 'step']);
  });

  it('a MOUNT FAILURE falls back to the house SVG and BADGES the downgrade', () => {
    fixture.componentRef.setInput('state', mixedState());
    fixture.detectChanges();

    const group = component.groupsOf('F4')[0];
    component.onMountFailed(group.id, 'vendor unavailable');
    fixture.detectChanges();

    const badge = fixture.nativeElement.querySelector('[data-test="panel-fallback-badge"]');
    expect(badge).not.toBeNull();
    expect((badge.textContent ?? '')).toContain('vendor unavailable');
    // The house SVG fallback is rendered.
    expect(fixture.nativeElement.querySelector('[data-test="house-svg"]')).not.toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════
// PHASE 6 (tasks 6.2 / 6.3) — the per-unit panel AXES
// ═══════════════════════════════════════════════════════════════════════

describe('MetricsCentralTabComponent — the per-unit panel axes (phase 6, 6.2/6.3)', () => {
  let fixture: ComponentFixture<MetricsCentralTabComponent>;
  let component: MetricsCentralTabComponent;

  /**
   * The F9 shape from the WIRE (w2.py:120-150): a counter AND a 0/1
   * gauge. Unlike the F4 fixture above (two counts), this family's two
   * metrics carry DIFFERENT UNITS after the vocabulary is populated —
   * so the family's two panels must differ in unit as well as kind.
   */
  const F9_MIXED_FAMILY: MetricsFamily = {
    id: 'F9',
    names: [
      {
        name: 'ensemble_readiness_probe_total',
        kind: 'counter',
        labels: null,
        help: '',
        buckets: null,
        cardinality_class: 'closed',
        monotonic: true,
        chart_role: 'step',
      },
      {
        name: 'ensemble_readiness_grace_active',
        kind: 'gauge',
        labels: null,
        help: '',
        buckets: null,
        cardinality_class: 'closed',
        monotonic: false,
        chart_role: 'line',
      },
    ],
    live_series_count: 2,
    availability: { state: 'live' },
    role: 'chart',
    source_idiom: 'registry',
    durability: 'process',
    cardinality_contribution: 1,
  };

  function f9State(): MetricsTabState {
    return state({
      families: [F9_MIXED_FAMILY],
      series: [
        {
          name: 'ensemble_readiness_probe_total',
          labels: {},
          kind: 'counter',
          monotonic: true,
          points: [
            [1_700_000_000, 1],
            [1_700_000_015, 2],
          ],
        },
        {
          name: 'ensemble_readiness_grace_active',
          labels: {},
          kind: 'gauge',
          monotonic: false,
          points: [
            [1_700_000_000, 0],
            [1_700_000_015, 1],
          ],
        },
      ],
    });
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsCentralTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsCentralTabComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('entry', metricsCentralTab('runtime'));
  });

  afterEach(() => fixture.destroy());

  it('the F9 counter + 0/1 gauge yield TWO panels with DIFFERENT UNITS (6.3)', () => {
    fixture.componentRef.setInput('state', f9State());
    fixture.detectChanges();

    const panels = fixture.nativeElement.querySelectorAll(
      '[data-test="central-tab-panel"]',
    ) as NodeListOf<HTMLElement>;
    expect(panels.length).toBe(2);
    const units = Array.from(panels).map((panel) => panel.getAttribute('data-unit'));
    expect(units.sort()).toEqual(['count', 'flag']);
    const kinds = Array.from(panels).map((panel) => panel.getAttribute('data-kind'));
    expect(kinds.sort()).toEqual(['line', 'step']);
    // The two panels' units DIFFER — the mixed-kind family never shares
    // one unit axis.
    expect(new Set(units).size).toBe(2);
  });

  it('each F9 panel hands its unit to the renderer (the unit-labeled value axis, 6.2)', () => {
    fixture.componentRef.setInput('state', f9State());
    fixture.detectChanges();

    const units = component.groupsOf('F9').map((group) => group.unit);
    expect(units.sort()).toEqual(['count', 'flag']);
    for (const group of component.groupsOf('F9')) {
      const data = component.panelData(group);
      // The render data's unit is the panel's — the Chart.js wrapper
      // suffixes it onto the value axis ticks (its own spec pins the
      // callback shape); 'unit unknown' is never silently substituted.
      expect(data.unit).toBe(group.unit);
      expect(data.unit).not.toBe('unit unknown');
    }
  });

  it('an UNMAPPED metric renders `unit unknown` on its panel — never a guess (6.3)', () => {
    const unmapped: MetricsFamily = {
      ...F9_MIXED_FAMILY,
      id: 'F99',
      names: [
        {
          name: 'ensemble_metric_the_lane_never_mapped',
          kind: 'gauge',
          labels: null,
          help: '',
          buckets: null,
          cardinality_class: 'closed',
          monotonic: false,
          chart_role: 'line',
        },
      ],
    };
    fixture.componentRef.setInput('entry', metricsCentralTab('runtime'));
    fixture.componentRef.setInput(
      'state',
      state({
        families: [unmapped],
        series: [
          {
            name: 'ensemble_metric_the_lane_never_mapped',
            labels: {},
            kind: 'gauge',
            monotonic: false,
            points: [
              [1_700_000_000, 1],
              [1_700_000_015, 2],
            ],
          },
        ],
      }),
    );
    fixture.detectChanges();

    const panels = fixture.nativeElement.querySelectorAll(
      '[data-test="central-tab-panel"]',
    ) as NodeListOf<HTMLElement>;
    expect(panels.length).toBe(1);
    // The honest placeholder — the spec FORBIDS inference, so an
    // unmapped name renders `unit unknown` on the axis.
    expect(panels[0].getAttribute('data-unit')).toBe('unit unknown');
  });
});
