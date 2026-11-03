// The METRICS PAGE SHELL spec (P5 task 5.4/5.7/5.8 · D1 pack
// `metrics-fe-page`).
//
// House pattern (mirrors `resources.component.spec.ts`): a logic-mirror
// class + production source pins + a light TestBed render.
//
// The load-bearing pins here:
//
//   * the availability-driven EMPTY STATE (P5-M4): the panel appears
//     EXACTLY when no family is live — a catalog whose only family is
//     DARK shows the panel, a catalog with a LIVE family does NOT (even
//     when no series has been published);
//   * the panel COPY (P5-M10 / AM-19(d)): it never says "disabled" and
//     never says "error" — the door is never switch-gated;
//   * a 200-EMPTY response never renders the error banner.

import { readFileSync } from 'fs';
import { join } from 'path';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Subject } from 'rxjs';

import { MetricsService } from '../../services/metrics.service';
import {
  MetricsFamiliesPayload,
  MetricsFamily,
} from '../../models/metrics.model';
import { MetricsComponent, MetricsSection } from './metrics.component';
import { OverviewTabComponent } from './overview/overview-tab.component';
import { ChartsTabComponent } from './charts/charts-tab.component';
import { FamiliesTabComponent } from './families/families-tab.component';

const metricsDir = join(__dirname);
const componentSrc = readFileSync(join(metricsDir, 'metrics.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(metricsDir, 'metrics.component.html'), 'utf-8');

/** Mirror of the production section registry — verbatim. */
class MetricsShellMirror {
  readonly sections: readonly MetricsSection[] = [
    { id: 'overview', label: 'Overview', component: OverviewTabComponent },
    { id: 'charts', label: 'Charts', component: ChartsTabComponent },
    { id: 'families', label: 'Families', component: FamiliesTabComponent },
  ];
}

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

function family(overrides: Partial<MetricsFamily> = {}): MetricsFamily {
  return {
    id: 'F1',
    names: [
      {
        name: 'ensemble_loop_lag_seconds',
        kind: 'gauge',
        labels: { stat: ['p50', 'p95'] },
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

function payload(families: readonly MetricsFamily[]): MetricsFamiliesPayload {
  return { ...envelope(), families, truncated: false };
}

/** A stub service whose calls never resolve (no HTTP in this spec). */
class StubMetricsService {
  readonly families$ = new Subject<MetricsFamiliesPayload>();
  readonly snapshot$ = new Subject<unknown>();
  readonly series$ = new Subject<unknown>();

  getFamilies() {
    return this.families$.asObservable();
  }
  getSnapshot() {
    return this.snapshot$.asObservable();
  }
  getSeries() {
    return this.series$.asObservable();
  }
}

describe('MetricsComponent — the section registry', () => {
  it('carries exactly the three tabs, in order', () => {
    const shell = new MetricsShellMirror();
    expect(shell.sections).toHaveLength(3);
    expect(shell.sections.map((section) => section.id)).toEqual([
      'overview',
      'charts',
      'families',
    ]);
    expect(shell.sections.map((section) => section.label)).toEqual([
      'Overview',
      'Charts',
      'Families',
    ]);
  });

  it('the production registry declares exactly three entries', () => {
    const registryBlock = componentSrc.slice(componentSrc.indexOf('readonly sections'));
    const entryMatches =
      registryBlock.match(/\{\s*id:\s*'(overview|charts|families)'/g) ?? [];
    expect(entryMatches).toHaveLength(3);
  });

  it('the template iterates the registry (a hard-coded tab would silently die)', () => {
    expect(templateSrc).toMatch(/@for\s*\(\s*section\s+of\s+sections\s*;/);
    expect(templateSrc).toContain('ngComponentOutlet');
    expect(templateSrc).toContain('mat-tab-group');
  });

  it('the shell imports the tab module and the dynamic outlet', () => {
    expect(componentSrc).toContain('MatTabsModule');
    expect(componentSrc).toContain('NgComponentOutlet');
  });
});

describe('MetricsComponent — the availability-driven empty state (P5-M4 / P5-M10)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;
  let stub: StubMetricsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    stub = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(MetricsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('shows the empty panel when NO family is live (a dark family is not live)', () => {
    component.families.set([
      family({
        id: 'F6',
        live_series_count: 0,
        availability: { state: 'dark', reason: 'census_gated' },
      }),
    ]);
    component.catalogLoaded.set(true);
    fixture.detectChanges();
    expect(component.anyFamilyLive()).toBe(false);
    expect(component.showEmptyPanel()).toBe(true);
    expect(fixture.nativeElement.querySelector('[data-test="metrics-empty-panel"]')).not.toBeNull();
  });

  it('does NOT show the empty panel when a family IS live', () => {
    component.families.set([family()]);
    component.catalogLoaded.set(true);
    fixture.detectChanges();
    expect(component.anyFamilyLive()).toBe(true);
    expect(component.showEmptyPanel()).toBe(false);
    expect(fixture.nativeElement.querySelector('[data-test="metrics-empty-panel"]')).toBeNull();
  });

  it('does NOT show the empty panel while the catalog is still loading', () => {
    component.catalogLoaded.set(false);
    component.families.set([]);
    fixture.detectChanges();
    expect(component.showEmptyPanel()).toBe(false);
  });

  it('a 200-EMPTY catalog shows the empty panel, never the error banner', () => {
    component.families.set([]);
    component.catalogLoaded.set(true);
    fixture.detectChanges();
    expect(component.catalogError()).toBeNull();
    expect(component.showEmptyPanel()).toBe(true);
    expect(fixture.nativeElement.querySelector('[data-test="metrics-catalog-error"]')).toBeNull();
  });

  it('the panel copy says neither "disabled" nor "error" (D-8 / AM-19(d))', () => {
    const copy = component.emptyCopy.toLowerCase();
    expect(copy).not.toContain('disabled');
    expect(copy).not.toContain('error');
    expect(copy).toContain('no metrics available yet');
  });

  it('a failed catalog load is an ERROR state, not an empty state', () => {
    component.catalogError.set('The metrics door is unreachable.');
    component.catalogLoaded.set(true);
    fixture.detectChanges();
    expect(component.showEmptyPanel()).toBe(false);
    expect(fixture.nativeElement.querySelector('[data-test="metrics-catalog-error"]')).not.toBeNull();
  });

  it('a 403 scope miss banners the SCOPE copy, not the unreachable copy (A-5)', () => {
    component.loadCatalog();
    stub.families$.error({
      status: 403,
      error: { detail: { error: 'SCOPE_INSUFFICIENT', message: 'Token lacks required scope' } },
    });
    fixture.detectChanges();
    const banner = fixture.nativeElement.querySelector('[data-test="metrics-catalog-error"]');
    expect(banner).not.toBeNull();
    const text = (banner.textContent ?? '').trim();
    expect(text).toContain('maintenance:read scope');
    expect(text).not.toContain('unreachable');
    // The last-good catalog is retained in every branch.
    expect(component.catalogStale()).toBe(false);
  });

  it('a 503 fail-closed door banners the AUTH copy, not the unreachable copy (A-5)', () => {
    component.loadCatalog();
    stub.families$.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED', message: 'Auth not configured (fail-closed)' } },
    });
    fixture.detectChanges();
    const text = (
      fixture.nativeElement.querySelector('[data-test="metrics-catalog-error"]')?.textContent ?? ''
    ).trim();
    expect(text).toContain('fail-closed');
    expect(text).not.toContain('unreachable');
  });

  it('a transport failure banners the unreachable copy (A-5)', () => {
    component.loadCatalog();
    stub.families$.error({ status: 0 });
    fixture.detectChanges();
    // The banner TEXT span only — the banner also carries the Retry CTA.
    const text = (
      fixture.nativeElement.querySelector(
        '[data-test="metrics-catalog-error"] .metrics-banner-text',
      )?.textContent ?? ''
    ).trim();
    expect(text).toBe('The metrics door is unreachable.');
  });

  it('surfaces a bounded catalog page (the W-4 truncated flag is never silently dropped)', () => {
    component.loadCatalog();
    stub.families$.next(payload([]));
    fixture.detectChanges();
    expect(component.catalogTruncated()).toBe(false);
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-catalog-truncated"]'),
    ).toBeNull();

    component.loadCatalog();
    stub.families$.next({ ...payload([family()]), truncated: true, next_cursor: 'opaque' });    fixture.detectChanges();
    expect(component.catalogTruncated()).toBe(true);
    const hint = fixture.nativeElement.querySelector('[data-test="metrics-catalog-truncated"]');
    expect(hint).not.toBeNull();
    expect((hint.textContent ?? '').toLowerCase()).toContain('bounded');
  });
});

describe('MetricsComponent — the chart-primary projection', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('projects ONLY the chart-primary families', () => {
    component.families.set([
      family({ id: 'F1', role: 'chart' }),
      family({
        id: 'F9',
        role: 'stat',
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
      }),
    ]);
    expect(component.chartSpecs().map((spec) => spec.name)).toEqual([
      'ensemble_loop_lag_seconds',
    ]);
  });

  it('carries the rendering mode of each series (a counter renders STEP)', () => {
    component.families.set([
      family({
        id: 'F4',
        role: 'chart',
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
    ]);
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
    component.families.set([family({ id: 'F1', role: 'chart', names })]);
    expect(component.chartSpecs().length).toBeLessThanOrEqual(50);
  });
});

describe('MetricsComponent — the runtime render', () => {
  let fixture: ComponentFixture<MetricsComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsComponent);
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('renders one tab label per registry entry', () => {
    const labels = fixture.debugElement
      .queryAll(By.css('[role="tab"]'))
      .map((tab) => (tab.nativeElement.textContent ?? '').trim());
    expect(labels.length).toBe(3);
    expect(labels.some((label) => label.includes('Overview'))).toBe(true);
    expect(labels.some((label) => label.includes('Charts'))).toBe(true);
    expect(labels.some((label) => label.includes('Families'))).toBe(true);
  });
});
