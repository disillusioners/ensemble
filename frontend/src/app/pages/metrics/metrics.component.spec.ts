// The METRICS PAGE SHELL spec (P5 task 5.4/5.7/5.8 · D1 pack
// `metrics-fe-page`; T1 phase 2 re-points it at the 7-tab shell).
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
//
// ── THE PHASE-2 RE-POINTING (2.9) ──────────────────────────────────────
//
// Every assertion that still applies is PRESERVED. The assertions that
// described the retired 3-tab shape are RE-POINTED, never deleted:
//
//   | was                              | now                                   |
//   |----------------------------------|---------------------------------------|
//   | the 3-entry section registry      | the manifest-driven 7-tab list      |
//   | the production registry declared 3| the production source drives the list |
//   | the template iterated `sections` | the template iterates `tabs`         |
//   | one tab label per registry entry  | one tab label per manifest entry (7) |
//   | the chart-primary projection      | the same projection, in the TAB BODY |
//
// The chart-primary projection MOVED to the tab body (each tab projects
// ITS OWN bound families); its three assertions are preserved there, in
// `tabs/central-tab.component.spec.ts`.

import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { ActivatedRoute, ParamMap, Router, convertToParamMap } from '@angular/router';
import { BehaviorSubject, Subject } from 'rxjs';

import { MetricsService } from '../../services/metrics.service';
import {
  MetricsFamiliesPayload,
  MetricsFamily,
} from '../../models/metrics.model';
import { METRICS_ROUTE_CHUNK_MARKER, MetricsComponent } from './metrics.component';
import { METRICS_TABS } from './tabs.manifest';

const metricsDir = join(__dirname);
const componentSrc = readFileSync(join(metricsDir, 'metrics.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(metricsDir, 'metrics.component.html'), 'utf-8');

/** Mirror of the production tab list — verbatim, from the manifest. */
class MetricsShellMirror {
  readonly tabs = METRICS_TABS;
}

function envelope(activationOverrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    vocabulary_version: 2,
    generated_at_utc: '2026-11-02T09:00:00+00:00',
    restart_epoch: 'abc123',
    activation: {
      listener_enabled: false,
      census_enabled: true,
      query_api: 'auth-gated-always-on',
      ...activationOverrides,
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

function payload(
  families: readonly MetricsFamily[],
  activationOverrides: Record<string, unknown> = {},
): MetricsFamiliesPayload {
  return { ...envelope(activationOverrides), families, truncated: false };
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

/** The router stub — it replays the parameter back, like the real one. */
class StubRouter {
  route: StubRoute | null = null;
  readonly navigations: { commands: unknown; extras: unknown }[] = [];

  navigate(commands: unknown, extras: unknown): Promise<boolean> {
    this.navigations.push({ commands, extras });
    const queryParams =
      (extras as { queryParams?: Record<string, string | null> }).queryParams ?? {};
    const next: Record<string, string> = {};
    for (const [key, value] of Object.entries(queryParams)) {
      if (value !== null && value !== undefined) {
        next[key] = String(value);
      }
    }
    this.route?.params.next(convertToParamMap(next));
    return Promise.resolve(true);
  }
}

/** The route stub — its parameter map is a replaying subject. */
class StubRoute {
  readonly params = new BehaviorSubject<ParamMap>(convertToParamMap({}));

  get queryParamMap() {
    return this.params.asObservable();
  }

  get snapshot() {
    return { queryParamMap: this.params.value };
  }
}

describe('MetricsComponent — the manifest-driven tab list', () => {
  it('carries exactly the eight tabs, in the manifest order', () => {
    const shell = new MetricsShellMirror();
    expect(shell.tabs).toHaveLength(8);
    expect(shell.tabs.map((tab) => tab.id)).toEqual([
      'resource',
      'runtime',
      'db-pool',
      'jobs',
      'instances',
      'checkpoint',
      'mcp',
      'nest-reports',
    ]);
    expect(shell.tabs.map((tab) => tab.label)).toEqual([
      'Resources',
      'Runtime',
      'DB Pool',
      'Jobs / Queue',
      'Instances',
      'Checkpoint',
      'MCP',
      'Nest Reports',
    ]);
  });

  it('the production shell drives the list from the manifest (no local registry)', () => {
    // The old 3-entry local registry is gone — the manifest is the only
    // tab source.
    expect(componentSrc).toContain('METRICS_TAB_BINDINGS');
    expect(componentSrc).not.toMatch(/readonly sections\b/);
    expect(componentSrc).not.toMatch(/\{\s*id:\s*'(overview|charts|families)'/);
  });

  it('the template iterates the manifest-driven list (a hard-coded tab would silently die)', () => {
    expect(templateSrc).toMatch(/@for\s*\(\s*tab\s+of\s+tabs\s*;/);
    expect(templateSrc).toContain('ngComponentOutlet');
    expect(templateSrc).toContain('mat-tab-group');
  });

  it('the shell imports the tab module and the dynamic outlet', () => {
    expect(componentSrc).toContain('MatTabsModule');
    expect(componentSrc).toContain('NgComponentOutlet');
  });

  it('the shell exports the ROUTE-CHUNK MARKER constant (2.13 / B3)', () => {
    expect(METRICS_ROUTE_CHUNK_MARKER).toBe('ensemble-metrics-route-v1');
    expect(componentSrc).toContain("export const METRICS_ROUTE_CHUNK_MARKER = 'ensemble-metrics-route-v1'");
  });
});

describe('MetricsComponent — the dead `active` input is GONE (2.8 / AM-23)', () => {
  it('the pinned residue check returns ZERO hits', () => {
    // The pinned command's scope, executed in-process: the families
    // directory + the shell source. The house command is
    //   grep -rn "\bactive\b" frontend/src/app/pages/metrics/families/ \
    //     frontend/src/app/pages/metrics/metrics.component.ts
    const files = [
      join(metricsDir, 'metrics.component.ts'),
      ...readdirSync(join(metricsDir, 'families')).map((name) =>
        join(metricsDir, 'families', name),
      ),
    ];
    const hits: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, 'utf-8');
      source.split('\n').forEach((line, index) => {
        if (/\bactive\b/.test(line)) {
          hits.push(`${file}:${index + 1}:${line.trim()}`);
        }
      });
    }
    expect(hits).toEqual([]);
  });

  it('the catalog component declares no `active` input', () => {
    const catalogSrc = readFileSync(
      join(metricsDir, 'families', 'families-tab.component.ts'),
      'utf-8',
    );
    expect(catalogSrc).not.toMatch(/readonly active\s*=/);
    expect(catalogSrc).not.toMatch(/input\(false\)/);
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
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
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
    // ── RE-POINTED BY PHASE 8 (task 8.2) ───────────────────────────
    // The panel is CLASS-driven (the class selects the copy SSOT entry
    // and the Retry policy), so the failure is set as a class. The leg
    // keeps its intent: a failed load is an ERROR state, never the
    // empty panel.
    component.catalogError.set('The metrics door is unreachable.');
    component.catalogErrorCode.set('network');
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

describe('MetricsComponent — the runtime render', () => {
  let fixture: ComponentFixture<MetricsComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsComponent);
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('renders one tab label per manifest entry', () => {
    const labels = fixture.debugElement
      .queryAll(By.css('[role="tab"]'))
      .map((tab) => (tab.nativeElement.textContent ?? '').trim());
    expect(labels.length).toBe(8);
    for (const tab of METRICS_TABS) {
      expect(labels.some((label) => label.includes(tab.label))).toBe(true);
    }
  });

  it('the old 3-tab shape is GONE (no Overview / Charts / Families tab)', () => {
    const labels = fixture.debugElement
      .queryAll(By.css('[role="tab"]'))
      .map((tab) => (tab.nativeElement.textContent ?? '').trim());
    expect(labels.some((label) => label.includes('Overview'))).toBe(false);
    expect(labels.some((label) => label.includes('Charts'))).toBe(false);
    expect(labels.some((label) => label.includes('Families'))).toBe(false);
  });
});

describe('MetricsComponent — the DEGRADED registry read (the registry_read_ok mirror)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;
  let stub: StubMetricsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
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

  it('a FAILED registry read renders the DISTINCT degraded badge', () => {
    component.loadCatalog();
    stub.families$.next(payload([], { registry_read_ok: false }));
    fixture.detectChanges();
    expect(component.catalogRegistryDegraded()).toBe(true);
    const badge = fixture.nativeElement.querySelector(
      '[data-test="metrics-registry-degraded"]',
    );
    expect(badge).not.toBeNull();
    expect((badge.textContent ?? '').toLowerCase()).toContain('incomplete');
  });

  it('a degraded read renders NEITHER the error banner NOR the empty panel', () => {
    component.loadCatalog();
    stub.families$.next(payload([], { registry_read_ok: false }));
    fixture.detectChanges();
    expect(component.showEmptyPanel()).toBe(false);
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-empty-panel"]'),
    ).toBeNull();
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-catalog-error"]'),
    ).toBeNull();
  });

  it('a HEALTHY 200-empty payload renders NEITHER the badge NOR the banner', () => {
    // The 200-EMPTY pin (the house's "never the error banner" rule) — the
    // degraded surface must not disturb it.
    component.loadCatalog();
    stub.families$.next(payload([]));
    fixture.detectChanges();
    expect(component.catalogRegistryDegraded()).toBe(false);
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-registry-degraded"]'),
    ).toBeNull();
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-catalog-error"]'),
    ).toBeNull();
    expect(component.showEmptyPanel()).toBe(true);
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-empty-panel"]'),
    ).not.toBeNull();
  });

  it('an ABSENT registry_read_ok reads as HEALTHY (the default-safe predicate)', () => {
    component.loadCatalog();
    stub.families$.next(payload([], {}));
    fixture.detectChanges();
    expect(component.catalogRegistryDegraded()).toBe(false);
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-registry-degraded"]'),
    ).toBeNull();
  });

  it('the degraded state is PER-LOAD — a subsequent healthy load clears it', () => {
    component.loadCatalog();
    stub.families$.next(payload([], { registry_read_ok: false }));
    fixture.detectChanges();
    expect(component.catalogRegistryDegraded()).toBe(true);

    component.loadCatalog();
    stub.families$.next(payload([family()]));
    fixture.detectChanges();
    expect(component.catalogRegistryDegraded()).toBe(false);
    expect(
      fixture.nativeElement.querySelector('[data-test="metrics-registry-degraded"]'),
    ).toBeNull();
  });

  it('the source carries the degraded guard on showEmptyPanel', () => {
    // The production-source pin: the guard must live on the computed
    // itself, not only in the template.
    const guard = componentSrc.slice(componentSrc.indexOf('readonly showEmptyPanel'));
    const block = guard.slice(0, guard.indexOf(');'));
    expect(block).toContain('catalogRegistryDegraded');
  });

  it('the degraded badge never reuses the transport-failure banner element', () => {
    // The DISTINCT surface is the whole point: the error banner carries
    // transport-failure semantics the 200-EMPTY pin reserves.
    const template = templateSrc;
    expect(template).toContain('data-test="metrics-registry-degraded"');
    const degradedBlock = template.slice(
      template.indexOf('metrics-registry-degraded'),
    );
    const block = degradedBlock.slice(0, degradedBlock.indexOf('}'));
    expect(block).not.toContain('metrics-catalog-error');
  });

  it('the degraded copy asserts the FACT and never attributes a cause', () => {
    const copy = component.registryDegradedCopy.toLowerCase();
    expect(copy).toContain('incomplete');
    expect(copy).toContain('unknown');
    for (const token of ['r-i', 'r-ii', 'capture_active', 'census inverted']) {
      expect(copy).not.toContain(token);
    }
  });
});

describe('MetricsComponent — the `?tab=` deep link (2.6)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;
  let route: StubRoute;
  let router: StubRouter;

  async function build(initial: Record<string, string>): Promise<void> {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
      ],
    }).compileComponents();
    route = TestBed.inject(ActivatedRoute) as unknown as StubRoute;
    router = TestBed.inject(Router) as unknown as StubRouter;
    router.route = route;
    route.params.next(convertToParamMap(initial));
    fixture = TestBed.createComponent(MetricsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  afterEach(() => {
    fixture?.destroy();
  });

  it('a KNOWN id selects that tab', async () => {
    await build({ tab: 'mcp' });
    expect(component.activeTabId()).toBe('mcp');
    expect(component.activeIndex()).toBe(6);
  });

  it('an UNKNOWN id falls back to the default tab (tab 1)', async () => {
    await build({ tab: 'no-such-tab' });
    expect(component.activeTabId()).toBe('resource');
    expect(component.activeIndex()).toBe(0);
  });

  it('the LEGACY id `overview` falls back to tab 1', async () => {
    await build({ tab: 'overview' });
    expect(component.activeTabId()).toBe('resource');
  });

  it('the LEGACY id `charts` falls back to tab 1', async () => {
    await build({ tab: 'charts' });
    expect(component.activeTabId()).toBe('resource');
  });

  it('the LEGACY id `families` falls back to tab 1', async () => {
    await build({ tab: 'families' });
    expect(component.activeTabId()).toBe('resource');
  });

  it('an ABSENT parameter selects the default tab (tab 1)', async () => {
    await build({});
    expect(component.activeTabId()).toBe('resource');
  });

  it('a legacy `?tab=overview` lands on tab 1 with NO navigation storm', async () => {
    await build({ tab: 'overview' });
    expect(component.activeTabId()).toBe('resource');
    // ONE rewrite — the parameter becomes known, and a known parameter
    // writes nothing (no loop, no history spam).
    expect(router.navigations.length).toBe(1);
    const extras = router.navigations[0].extras as {
      queryParams: Record<string, string>;
      queryParamsHandling: string;
      replaceUrl: boolean;
    };
    expect(extras.queryParams).toEqual({ tab: 'resource' });
    expect(extras.queryParamsHandling).toBe('merge');
    expect(extras.replaceUrl).toBe(true);
  });

  it('an ABSENT parameter writes nothing', async () => {
    await build({});
    expect(router.navigations.length).toBe(0);
  });

  it('the tab change writes the parameter back to the URL', async () => {
    await build({});
    component.onTabChange(2);
    expect(component.activeTabId()).toBe('db-pool');
    expect(router.navigations.length).toBe(1);
    const extras = router.navigations[0].extras as { queryParams: Record<string, string> };
    expect(extras.queryParams).toEqual({ tab: 'db-pool' });
  });

  it('re-selecting the SAME tab writes nothing (no storm)', async () => {
    await build({});
    component.onTabChange(0);
    expect(router.navigations.length).toBe(0);
  });
});

describe('MetricsComponent — the PER-SOURCE state model (2.11 / AM-1)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;
  let stub: StubMetricsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
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

  it('a failing CENTRAL source does NOT blank the custom tab\'s state', () => {
    // The custom tab has its own state (its own source).
    component.applyTabState('resource', {
      doorState: 'ok',
      families: [family({ id: 'F0.3' })],
    });

    // The central door fails.
    component.loadCatalog();
    stub.families$.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED', message: 'fail-closed' } },
    });
    fixture.detectChanges();

    // The custom tab is untouched.
    expect(component.tabStates()['resource'].doorState).toBe('ok');
    expect(component.tabStates()['resource'].families.length).toBe(1);
    // ── RE-POINTED BY PHASE 8 (task 8.3) ───────────────────────────
    // The central tabs carry the failure as their POSTURE, and the
    // FAILURE CLASS lives on the frame — the central state is rendered
    // ONCE, so it is no longer published into every central tab's
    // error class (that was the duplicate banner). The leg keeps its
    // intent — the central failure reaches the central tabs — and now
    // asserts the posture plus the frame's class.
    expect(component.tabStates()['runtime'].doorState).toBe('auth_not_configured');
    expect(component.tabStates()['runtime'].errorClass).toBeNull();
    expect(component.catalogErrorCode()).toBe('auth_not_configured');
  });

  it('a failing CUSTOM source does NOT blank a central tab\'s state', () => {
    component.loadCatalog();
    stub.families$.next(payload([family({ id: 'F9' })]));
    fixture.detectChanges();
    expect(component.tabStates()['runtime'].families.length).toBe(1);

    // The custom source fails.
    component.applyTabState('resource', { doorState: 'network', errorClass: 'network' });

    expect(component.tabStates()['runtime'].doorState).toBe('ok');
    expect(component.tabStates()['runtime'].errorClass).toBeNull();
    expect(component.tabStates()['runtime'].families.length).toBe(1);
    expect(component.tabStates()['resource'].errorClass).toBe('network');
  });

  it('a tab body can write ONLY its own tab\'s state', () => {
    const reporter = component.tabInputs()['runtime']['onState'] as (
      patch: Record<string, unknown>,
    ) => void;
    reporter({ doorState: 'ok', errorClass: null });
    expect(component.tabStates()['runtime'].doorState).toBe('ok');
    // The custom tab's state was never written by the central tab.
    expect(component.tabStates()['resource']).toBeUndefined();
  });

  it('the last-good families are RETAINED when the door fails', () => {
    component.loadCatalog();
    stub.families$.next(payload([family({ id: 'F9' })]));
    fixture.detectChanges();
    component.loadCatalog();
    stub.families$.error({ status: 0 });
    fixture.detectChanges();
    expect(component.tabStates()['runtime'].families.length).toBe(1);
  });
});

describe('MetricsComponent — the family-catalog drawer (2.7)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('the drawer is CLOSED by default and opens from the frame toolbar', () => {
    expect(fixture.nativeElement.querySelector('[data-test="metrics-catalog-drawer"]')).toBeNull();
    const toggle = fixture.nativeElement.querySelector(
      '[data-test="metrics-catalog-drawer-toggle"]',
    ) as HTMLElement;
    expect(toggle).not.toBeNull();
    toggle.click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="metrics-catalog-drawer"]')).not.toBeNull();
  });

  it('the drawer carries the catalog (the families tab is retired)', () => {
    component.families.set([family({ id: 'F1' })]);
    component.toggleDrawer();
    fixture.detectChanges();
    const drawer = fixture.nativeElement.querySelector(
      '[data-test="metrics-catalog-drawer"]',
    ) as HTMLElement;
    expect(drawer).not.toBeNull();
    expect(drawer.querySelector('[data-test="families-table"]')).not.toBeNull();
    expect(drawer.querySelector('[data-family="F1"]')).not.toBeNull();
  });

  it('the drawer closes again', () => {
    component.toggleDrawer();
    fixture.detectChanges();
    component.closeDrawer();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="metrics-catalog-drawer"]')).toBeNull();
  });
});

// ── THE PHASE-3 FRAME SURFACE (the range control + the baseline) ────────
//
// The load-bearing pins:
//
//   * the range control's OPTION SET is the union of the manifest's
//     ladders, and the options the ACTIVE tab cannot serve are DISABLED
//     (the visible enforcement — the door would clamp silently);
//   * the tab switch KEEPS the window when the new tab serves it and
//     SNAPS to that tab's default otherwise — and the snap is ANNOUNCED
//     (a silent snap is a lie);
//   * the baseline toggle is scoped to the central tabs and DISABLED with
//     its reason on the custom slot;
//   * crossing the 1 h guard AUTO-CLEARS the baseline WITH its reason;
//   * the fidelity disclosure is VISIBLE next to the baseline control.
describe('MetricsComponent — the range control (3.1)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  /** The rendered options of the frame's control, with their disabled flag. */
  function renderedOptions(): { value: string; disabled: boolean }[] {
    const select = fixture.nativeElement.querySelector(
      '[data-test="metrics-range-select"]',
    ) as HTMLSelectElement;
    expect(select).not.toBeNull();
    return Array.from(select.options).map((option) => ({
      value: option.value,
      disabled: option.disabled,
    }));
  }

  it('carries the UNION of the manifest\'s ladders', () => {
    expect(renderedOptions().map((option) => option.value)).toEqual([
      '15m',
      '30m',
      '1h',
      '2h',
      '24h',
      '7d',
    ]);
  });

  it('a CENTRAL tab shows 15m/30m/1h/2h with 24h/7d DISABLED', () => {
    component.onTabChange(1); // runtime — a central tab
    fixture.detectChanges();
    const options = renderedOptions();
    expect(options.filter((option) => !option.disabled).map((option) => option.value)).toEqual([
      '15m',
      '30m',
      '1h',
      '2h',
    ]);
    expect(options.find((option) => option.value === '24h')?.disabled).toBe(true);
    expect(options.find((option) => option.value === '7d')?.disabled).toBe(true);
  });

  it('the CUSTOM tab shows 15m/1h/24h/7d with 30m/2h DISABLED', () => {
    component.onTabChange(0); // resource — the custom slot
    fixture.detectChanges();
    const options = renderedOptions();
    expect(options.filter((option) => !option.disabled).map((option) => option.value)).toEqual([
      '15m',
      '1h',
      '24h',
      '7d',
    ]);
    expect(options.find((option) => option.value === '30m')?.disabled).toBe(true);
    expect(options.find((option) => option.value === '2h')?.disabled).toBe(true);
  });

  it('the frame\'s control is INERT on the custom slot, and says why (AM-19)', () => {
    component.onTabChange(0);
    fixture.detectChanges();
    const select = fixture.nativeElement.querySelector(
      '[data-test="metrics-range-select"]',
    ) as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    const note = fixture.nativeElement.querySelector('[data-test="metrics-range-control-note"]');
    expect(note).not.toBeNull();
    expect((note.textContent ?? '')).toContain('own range control');
  });

  it('an option the SELECTED tab cannot serve is NEVER applied', () => {
    component.onTabChange(1); // a central tab
    component.selectWindow('24h');
    expect(component.window()).not.toBe('24h');
  });
});

describe('MetricsComponent — the tab switch (3.2)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('KEEPS the window when the new tab serves it, and says NOTHING', () => {
    component.onTabChange(1); // runtime
    component.selectWindow('2h');
    component.onTabChange(2); // db-pool
    fixture.detectChanges();
    expect(component.window()).toBe('2h');
    expect(fixture.nativeElement.querySelector('[data-test="metrics-range-note"]')).toBeNull();
  });

  it('SNAPS to the new tab\'s default and ANNOUNCES the snap', () => {
    component.onTabChange(1); // runtime — a central tab
    component.selectWindow('2h');
    component.onTabChange(0); // resource — the custom slot
    fixture.detectChanges();
    expect(component.window()).toBe('1h');
    const note = fixture.nativeElement.querySelector('[data-test="metrics-range-note"]');
    expect(note).not.toBeNull();
    expect((note.textContent ?? '')).toContain('snapped');
    expect((note.textContent ?? '')).toContain('Resources');
  });

  it('a resources-only window SNAPS back onto the central default', () => {
    component.onTabChange(0); // resource — the custom slot
    component.selectWindow('24h');
    component.onTabChange(1); // runtime
    fixture.detectChanges();
    expect(component.window()).toBe('1h');
    expect(fixture.nativeElement.querySelector('[data-test="metrics-range-note"]')).not.toBeNull();
  });
});

describe('MetricsComponent — the baseline toggle (3.4 / 3.5 / 3.6)', () => {
  let fixture: ComponentFixture<MetricsComponent>;
  let component: MetricsComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: ActivatedRoute, useClass: StubRoute },
        { provide: Router, useClass: StubRouter },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('the toggle is LIVE on a central tab', () => {
    component.onTabChange(1);
    fixture.detectChanges();
    const toggle = fixture.nativeElement.querySelector(
      '[data-test="metrics-baseline-toggle"]',
    ) as HTMLInputElement;
    expect(toggle.disabled).toBe(false);
    component.setBaseline(true);
    fixture.detectChanges();
    expect(component.baselineOn()).toBe(true);
  });

  it('the CUSTOM slot renders the toggle DISABLED with its reason (3.6)', () => {
    component.onTabChange(0);
    fixture.detectChanges();
    const toggle = fixture.nativeElement.querySelector(
      '[data-test="metrics-baseline-toggle"]',
    ) as HTMLInputElement;
    expect(toggle.disabled).toBe(true);
    const reason = fixture.nativeElement.querySelector(
      '[data-test="metrics-baseline-disabled-reason"]',
    );
    expect(reason).not.toBeNull();
    expect((reason.textContent ?? '')).toContain(
      'the resources door serves a fixed 15m/1h/24h/7d vocabulary with no previous-window offset',
    );
  });

  it('crossing 1 h AUTO-CLEARS the baseline WITH its reason (3.5a)', () => {
    component.onTabChange(1);
    component.setBaseline(true);
    expect(component.baselineOn()).toBe(true);
    component.selectWindow('2h');
    fixture.detectChanges();
    expect(component.baselineOn()).toBe(false);
    const note = fixture.nativeElement.querySelector('[data-test="metrics-baseline-note"]');
    expect(note).not.toBeNull();
    expect((note.textContent ?? '')).toContain('1 h or less');
  });

  it('the baseline is REFUSED beyond 1 h, with the reason', () => {
    component.onTabChange(1);
    component.selectWindow('2h');
    component.setBaseline(true);
    expect(component.baselineOn()).toBe(false);
    expect(component.baselineNote()).toContain('1 h or less');
  });

  it('the FIDELITY DISCLOSURE is visible while the baseline is on (3.8)', () => {
    component.onTabChange(1);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="metrics-baseline-disclosure"]')).toBeNull();
    component.setBaseline(true);
    fixture.detectChanges();
    const disclosure = fixture.nativeElement.querySelector(
      '[data-test="metrics-baseline-disclosure"]',
    );
    expect(disclosure).not.toBeNull();
    expect((disclosure.textContent ?? '')).toContain('bucket can straddle the split');
  });

  it('the tab inputs carry the window, the baseline and the range reporter (3.3)', () => {
    component.onTabChange(1);
    component.selectWindow('30m');
    component.setBaseline(true);
    fixture.detectChanges();
    const inputs = component.tabInputs()['runtime'];
    expect(inputs['window']).toBe('30m');
    expect(inputs['baseline']).toBe(true);
    expect(typeof inputs['onRangeChange']).toBe('function');
  });
});
