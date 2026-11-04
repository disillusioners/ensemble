// The PHASE-8 DOOR-STATE spec (T1 phase 8 — empty state + honesty, D-2).
//
// Four pins carry the weight, one per task:
//
//   8.2 — the ACTIONABLE `auth_not_configured` state: cause, meaning,
//         action and the RUNBOOK POINTER, and ZERO Retry buttons. The
//         other classes keep their banner + Retry semantics. The
//         custom slot renders its OWN source's state (tab 1 stays lit
//         under a central-door failure).
//   8.3 — the state is rendered ONCE per surface: no page-level banner
//         AND a per-tab banner for the same failure.
//   8.4 — the copy states the TRUTH for the case that is happening: it
//         never claims retained last-good values when none are
//         rendered, and the STALE badge still appears when a last-good
//         exists.
//   8.5 — the door posture is a PAGE-SCOPED chip; the app shell's
//         GLOBAL health dot stays bound to `/api/health` (a red dot
//         would be a false global statement).
//
// The house pattern (mirrors `metrics.component.spec.ts`): a light
// TestBed render + production source pins.

import { readFileSync } from 'fs';
import { join } from 'path';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { BehaviorSubject, Subject } from 'rxjs';

import { MetricsService } from '../../services/metrics.service';
import {
  METRICS_DOOR_STATE_COPY,
  MetricsFamiliesPayload,
  MetricsFamily,
} from '../../models/metrics.model';
import { MetricsComponent } from './metrics.component';
import { MetricsDoorStateComponent } from './metrics-door-state.component';

const metricsDir = join(__dirname);
const templateSrc = readFileSync(join(metricsDir, 'metrics.component.html'), 'utf-8');
const componentSrc = readFileSync(join(metricsDir, 'metrics.component.ts'), 'utf-8');
const appHtmlSrc = readFileSync(join(__dirname, '..', '..', 'app.html'), 'utf-8');
const appTsSrc = readFileSync(join(__dirname, '..', '..', 'app.ts'), 'utf-8');

/** A stub service whose calls never resolve (no HTTP in this spec). */
class StubMetricsService {
  readonly families$ = new Subject<MetricsFamiliesPayload>();
  getFamilies() {
    return this.families$.asObservable();
  }
  getSnapshot() {
    return new Subject<unknown>().asObservable();
  }
  getSeries() {
    return new Subject<unknown>().asObservable();
  }
}

/** The router stub. */
class StubRouter {
  navigate(): Promise<boolean> {
    return Promise.resolve(true);
  }
}

/** The route stub — it replays the parameter back, like the real one. */
class StubRoute {
  readonly params = new BehaviorSubject(convertToParamMap({}));
  get queryParamMap() {
    return this.params.asObservable();
  }
  get snapshot() {
    return { queryParamMap: this.params.value };
  }
}

function envelope() {
  return {
    schema_version: 1,
    vocabulary_version: 2,
    generated_at_utc: '2026-11-04T00:00:00+00:00',
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
    names: [],
    live_series_count: 0,
    availability: { state: 'dark', reason: 'census_gated' },
    role: 'chart',
    source_idiom: 'gauge',
    durability: 'ephemeral',
    cardinality_contribution: 0,
    ...overrides,
  } as MetricsFamily;
}

function payload(families: readonly MetricsFamily[]): MetricsFamiliesPayload {
  return { ...envelope(), families, truncated: false } as MetricsFamiliesPayload;
}

describe('MetricsDoorStateComponent — the ACTIONABLE state (T1 phase 8, task 8.2)', () => {
  let fixture: ComponentFixture<MetricsDoorStateComponent>;
  let component: MetricsDoorStateComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsDoorStateComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsDoorStateComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('renders NOTHING when there is no class (a surface never carries an empty banner)', () => {
    fixture.componentRef.setInput('code', null);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="metrics-door-state"]')).toBeNull();
  });

  it('the AUTH state renders the cause, the meaning, the action and the RUNBOOK POINTER', () => {
    fixture.componentRef.setInput('code', 'auth_not_configured');
    fixture.detectChanges();
    const panel = fixture.nativeElement.querySelector('[data-test="metrics-door-state"]');
    expect(panel).not.toBeNull();
    const cause = fixture.nativeElement.querySelector('[data-test="metrics-door-state-cause"]');
    const meaning = fixture.nativeElement.querySelector('[data-test="metrics-door-state-meaning"]');
    const action = fixture.nativeElement.querySelector('[data-test="metrics-door-state-action"]');
    const pointer = fixture.nativeElement.querySelector('[data-test="metrics-door-state-pointer"]');
    expect(cause?.textContent ?? '').toContain('fail-closed');
    expect(meaning?.textContent ?? '').toContain('daemon configuration');
    expect((action?.textContent ?? '').trim().length).toBeGreaterThan(0);
    expect(pointer?.textContent ?? '').toContain('docs/runbooks/oauth2-auth.md');
  });

  it('the AUTH state renders ZERO Retry buttons (a config error is not transient)', () => {
    fixture.componentRef.setInput('code', 'auth_not_configured');
    fixture.componentRef.setInput('onRetry', () => undefined);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="metrics-door-state-retry"]')).toBeNull();
  });

  it('a RETRYABLE class keeps its banner + Retry semantics', () => {
    let fired = 0;
    fixture.componentRef.setInput('code', 'network');
    fixture.componentRef.setInput('onRetry', () => {
      fired += 1;
    });
    fixture.detectChanges();
    const retry = fixture.nativeElement.querySelector('[data-test="metrics-door-state-retry"]');
    expect(retry).not.toBeNull();
    retry?.click();
    fixture.detectChanges();
    expect(fired).toBe(1);
  });

  it('the NO-Retry classes render no Retry even when a handler is supplied', () => {
    for (const code of ['auth_required', 'auth_not_configured', 'unknown_family', 'bad_request', 'timeout']) {
      fixture.componentRef.setInput('code', code);
      fixture.componentRef.setInput('onRetry', () => undefined);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('[data-test="metrics-door-state-retry"]')).toBeNull();
    }
  });

  it('NO environment-variable name leaks into the rendered copy (the plan\'s risk row)', () => {
    for (const code of Object.keys(METRICS_DOOR_STATE_COPY)) {
      fixture.componentRef.setInput('code', code);
      fixture.detectChanges();
      const text = fixture.nativeElement.textContent ?? '';
      expect(text).not.toContain('ENSEMBLE_');
      expect(text).not.toContain('POSTGRES_');
    }
  });
});

describe('MetricsComponent — the door state renders ONCE (T1 phase 8, task 8.3)', () => {
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

  it('a failed central read renders EXACTLY ONE door-state panel on the page', () => {
    component.loadCatalog();
    stub.families$.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED', message: 'fail-closed' } },
    });
    fixture.detectChanges();
    const panels = fixture.nativeElement.querySelectorAll('[data-test="metrics-catalog-error"]');
    expect(panels.length).toBe(1);
    // And NO second panel in a tab body — the tab bodies do not each
    // re-render the central state.
    expect(fixture.nativeElement.querySelectorAll('[data-test="central-tab-error"]').length).toBe(0);
  });

  it('the frame template renders the state panel ONCE (a planted second banner fails)', () => {
    const occurrences = templateSrc.match(/<app-metrics-door-state/g) ?? [];
    expect(occurrences.length).toBe(1);
  });

  it('the drawer states the FACT and POINTS at the failure — it does not re-render the panel', () => {
    const drawerSrc = readFileSync(
      join(metricsDir, 'families', 'families-tab.component.ts'),
      'utf-8',
    );
    expect(drawerSrc).not.toContain('app-metrics-door-state');
    // The drawer's pointer line exists and the empty copy stays gated.
    expect(drawerSrc).toContain('data-test="families-error"');
    expect(drawerSrc).toContain('No families are registered.');
  });

  it('a CENTRAL failure does NOT blank the CUSTOM tab (tab 1 stays lit)', () => {
    component.applyTabState('resource', {
      doorState: 'ok',
      families: [family({ id: 'F0.3' })],
    });
    component.loadCatalog();
    stub.families$.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED', message: 'fail-closed' } },
    });
    fixture.detectChanges();
    // The custom tab's own state survives the central failure.
    expect(component.tabStates()['resource'].doorState).toBe('ok');
    expect(component.tabStates()['resource'].families.length).toBe(1);
    // And the central failure never reaches the custom tab's class.
    expect(component.tabStates()['resource'].errorClass).toBeNull();
  });
});

describe('MetricsComponent — the truthful copy (T1 phase 8, task 8.4)', () => {
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

  it('the frame NEVER claims retained last-good values in its failure copy', () => {
    component.loadCatalog();
    stub.families$.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED', message: 'fail-closed' } },
    });
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent ?? '';
    expect(text).not.toContain('the last known values are kept');
    expect(text).not.toContain('the last known trend is kept');
  });

  it('the STALE badge still appears when a last-good catalog exists', () => {
    component.loadCatalog();
    stub.families$.next(payload([family({ id: 'F1' })]));
    fixture.detectChanges();
    component.loadCatalog();
    stub.families$.error({ status: 0 });
    fixture.detectChanges();
    expect(component.catalogStale()).toBe(true);
    expect(fixture.nativeElement.querySelector('[data-test="metrics-catalog-stale"]')).not.toBeNull();
  });

  it('the two tab bodies choose their suffix by the FACT (a last-good or not)', () => {
    // The production sources carry the conditional — the unconditional
    // "the last known values are kept" is gone from both bodies.
    for (const file of [
      join(metricsDir, 'overview', 'overview-tab.component.ts'),
      join(metricsDir, 'charts', 'charts-tab.component.ts'),
    ]) {
      const source = readFileSync(file, 'utf-8');
      expect(source).not.toMatch(/copy\}\s*—\s*the last known/);
      expect(source).toContain('METRICS_NO_LAST_GOOD_SUFFIX');
    }
  });
});

describe('MetricsComponent — the PAGE-SCOPED posture chip (T1 phase 8, task 8.5)', () => {
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

  it('the metrics header carries the door-posture chip', () => {
    const chip = fixture.nativeElement.querySelector('[data-test="metrics-door-chip"]');
    expect(chip).not.toBeNull();
  });

  it('the chip names the FAIL-CLOSED posture when the door is fail-closed', () => {
    component.loadCatalog();
    stub.families$.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED', message: 'fail-closed' } },
    });
    fixture.detectChanges();
    const chip = fixture.nativeElement.querySelector('[data-test="metrics-door-chip"]');
    expect(chip?.getAttribute('data-door-posture')).toBe('fail-closed');
    expect(chip?.textContent ?? '').toContain('fail-closed');
  });

  it('the chip reads OK when the door answers', () => {
    component.loadCatalog();
    stub.families$.next(payload([family({ id: 'F1' })]));
    fixture.detectChanges();
    const chip = fixture.nativeElement.querySelector('[data-test="metrics-door-chip"]');
    expect(chip?.getAttribute('data-door-posture')).toBe('ok');
  });

  it('the GLOBAL health dot is UNCHANGED by a door failure (AM-21)', () => {
    // The dot is bound to `/api/health` — the daemon is HEALTHY when the
    // metrics door is deliberately fail-closed, so a red dot would be a
    // FALSE GLOBAL statement.
    expect(appHtmlSrc).toContain('[class.healthy]="health()!.status === \'healthy\'"');
    expect(appHtmlSrc).toContain('[class.unhealthy]="health()!.status !== \'healthy\'"');
    expect(appTsSrc).toContain('this.api.health()');
    // The metrics page NEVER touches the shell's dot.
    expect(componentSrc).not.toContain('status-dot');
    expect(templateSrc).not.toContain('status-dot');
  });

  it('a PLANTED "red global dot" would be caught (the dot class is bound to health alone)', () => {
    // The guard is the binding itself: the dot's class comes from the
    // health payload and nothing else. A variant that ORs a door state
    // into it would change the binding — so the pin reads the binding.
    const dotBinding = appHtmlSrc
      .split('\n')
      .filter((line) => line.includes('[class.healthy]') || line.includes('[class.unhealthy]'));
    expect(dotBinding.length).toBe(2);
    for (const line of dotBinding) {
      expect(line).toContain('health()!');
      expect(line).not.toContain('door');
      expect(line).not.toContain('metrics');
    }
  });
});
