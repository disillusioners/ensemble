// The CUSTOM SLOT spec (T1 phase 3, tasks 3.1 / 3.6 / 3.12; rebuilt FLAT
// in phase 5 — tasks 5.2 / 5.4 / 5.10 / 5.11 / 5.12).
//
// The load-bearing pins:
//
//   PHASE 3 (kept verbatim — the control and its guards):
//   * the body carries its OWN labelled range control ("Timeline range")
//     — the frame's central-sourced control does NOT govern it (AM-19);
//   * the option set is the frame's union with the options the resources
//     ladder cannot serve DISABLED (the visible enforcement);
//   * the mapping onto the resources ladder REFUSES a central-only window
//     — no `/api/resources/*` request can ever carry a window outside the
//     frozen `ResourcesWindow` union (the door would 400 on it);
//   * the retention-tier copy names the tiers — and the tiers are NEVER
//     offered as range options.
//
//   PHASE 5 (new):
//   * the body is a FLAT column — live strip → timeline panel → anomalies
//     panel, in that order, with NO nested tab group and no placeholder
//     (5.2 / 5.12);
//   * a range change issues EXACTLY ONE /history request and NONE to
//     /live or /self-cost (5.4);
//   * a window change discards an in-flight window/anomalies response and
//     NEVER discards the live strip's 8 s poll response (5.11 / AM-16);
//   * the body reads its OWN source only (AM-1 / 5.10): with the central
//     door refusing and the resources door answering 200, the body renders
//     real data and NO auth panel.

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Subject, of } from 'rxjs';
import { readFileSync } from 'fs';
import { join } from 'path';

import { ResourcesWindow, HistoryPayload, LivePayload, Anomaly } from '../../../models/resources.models';
import { MetricsRangeOption } from '../metrics-range';
import { MetricsService } from '../../../services/metrics.service';
import { ResourcesService } from '../../../services/resources.service';
import { LiveTabComponent } from '../resource/live/live-tab.component';
import { ResourceTabComponent } from './resource-tab.component';

// ── Fixtures ─────────────────────────────────────────────────────────

function makeHistory(overrides: Partial<HistoryPayload> = {}): HistoryPayload {
  return {
    ...overrides,
    points: [
      { t: '2026-11-03T10:00:00', daemon_cpu: 1, daemon_rss_mb: 1700, cgroup_mem_mb: 5800, threads: 40, instances_active: 5 },
      { t: '2026-11-03T10:15:00', daemon_cpu: 2, daemon_rss_mb: 1750, cgroup_mem_mb: 5850, threads: 42, instances_active: 5 },
    ],
    resolution_used: 'raw',
    rollup: 'none',
    truncated: false,
  };
}

function makeAnomaly(id: string): Anomaly {
  return {
    id,
    kind: 'cpu',
    t_start: '2026-11-03T09:30:00',
    t_end: '2026-11-03T09:40:00',
    peak: 75.5,
    baseline: 12,
    severity: 'medium',
    series_key: 'daemon_cpu',
    shape: 'spike',
  };
}

function makeLive(): LivePayload {
  return {
    ts: '2026-11-03T10:00:00',
    sample_level: 'full',
    daemon: { pid: 1, cpu_pct: 2, rss_mb: 1700, threads: 40, fds: 90, uptime_s: 1000 },
    cgroup: null,
    children: [],
    instances: [],
    attribution: { attributed_mb: 0, unattributed_mb: 0, coverage_pct: 0 },
    overruns: { count: 0, last_ts: null },
    self_cost: {
      cpu_pct: 2,
      cumulative_cpu_s: 1,
      buffers_bytes: 10,
      rss_budget_mb: 2048,
      threads: 40,
      samples: 5,
      ticks_skipped: 0,
      flush: { batches: 1, bytes: 10, errors: 0 },
      profiling: { active: false, capabilities: [] },
    },
  };
}

interface ResourcesMock {
  getLive: jest.Mock;
  getSelfCost: jest.Mock;
  getHistory: jest.Mock;
  getAnomalies: jest.Mock;
  getEvidence: jest.Mock;
  getTraceback: jest.Mock;
}

const resourcesMock: ResourcesMock = {
  getLive: jest.fn(),
  getSelfCost: jest.fn(),
  getHistory: jest.fn(),
  getAnomalies: jest.fn(),
  getEvidence: jest.fn(),
  getTraceback: jest.fn(),
};

async function createBody(
  inputs: Record<string, unknown> = {},
): Promise<{ fixture: ComponentFixture<ResourceTabComponent>; component: ResourceTabComponent }> {
  const fixture = TestBed.createComponent(ResourceTabComponent);
  for (const [key, value] of Object.entries(inputs)) {
    fixture.componentRef.setInput(key, value);
  }
  fixture.detectChanges(); // the effect runs — the window loads fire
  fixture.detectChanges(); // the synchronous of() responses render
  return { fixture, component: fixture.componentInstance };
}

// ═════════════════════════════════════════════════════════════════════
// PHASE 3 — the control + its guards (kept verbatim)
// ═════════════════════════════════════════════════════════════════════

describe('ResourceTabComponent — the panel\'s own range control (3.1 / AM-19)', () => {
  let fixture: ComponentFixture<ResourceTabComponent>;
  let component: ResourceTabComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResourceTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: ResourcesService, useValue: resourcesMock },
      ],
    }).compileComponents();
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [makeAnomaly('a')] }));
    resourcesMock.getLive.mockReturnValue(new Subject<LivePayload>().asObservable());
    resourcesMock.getSelfCost.mockReturnValue(new Subject<LivePayload>().asObservable());
    fixture = TestBed.createComponent(ResourceTabComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  /** The rendered options of the body's control, with their disabled flag. */
  function renderedOptions(): { value: string; disabled: boolean }[] {
    const select = fixture.nativeElement.querySelector(
      '[data-test="resource-range-select"]',
    ) as HTMLSelectElement;
    expect(select).not.toBeNull();
    return Array.from(select.options).map((option) => ({
      value: option.value,
      disabled: option.disabled,
    }));
  }

  it('the control is LABELLED "Timeline range" — it is the panel\'s own', () => {
    const label = fixture.nativeElement.querySelector('.resource-range-label');
    expect(label).not.toBeNull();
    expect((label.textContent ?? '').trim()).toBe('Timeline range');
  });

  it('shows 15m/1h/24h/7d with 30m/2h DISABLED', () => {
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

  it('the control edits the FRAME\'s value through the reporter', () => {
    const seen: MetricsRangeOption[] = [];
    fixture.componentRef.setInput('onRangeChange', (option: MetricsRangeOption) => {
      seen.push(option);
    });
    fixture.detectChanges();
    const select = fixture.nativeElement.querySelector(
      '[data-test="resource-range-select"]',
    ) as HTMLSelectElement;
    select.value = '24h';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(seen).toEqual(['24h']);
  });

  it('a DISABLED option is never handed to the frame', () => {
    const seen: MetricsRangeOption[] = [];
    fixture.componentRef.setInput('onRangeChange', (option: MetricsRangeOption) => {
      seen.push(option);
    });
    fixture.detectChanges();
    component.onRangeSelected('2h');
    expect(seen).toEqual([]);
  });
});

describe('ResourceTabComponent — the mapping guard (3.6)', () => {
  let fixture: ComponentFixture<ResourceTabComponent>;
  let component: ResourceTabComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResourceTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: ResourcesService, useValue: resourcesMock },
      ],
    }).compileComponents();
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [] }));
    resourcesMock.getLive.mockReturnValue(new Subject<LivePayload>().asObservable());
    resourcesMock.getSelfCost.mockReturnValue(new Subject<LivePayload>().asObservable());
    fixture = TestBed.createComponent(ResourceTabComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('maps the frame\'s value onto the resources ladder', () => {
    fixture.componentRef.setInput('window', '24h');
    fixture.detectChanges();
    expect(component.resourcesWindow()).toBe('24h');
  });

  it('REFUSES a central-only window — no request can carry it', () => {
    fixture.componentRef.setInput('window', '2h');
    fixture.detectChanges();
    expect(component.resourcesWindow()).toBeNull();
  });

  it('W = 2 h with the toggle forced on ⇒ NO over-ceiling request and NO baseline line', () => {
    fixture.componentRef.setInput('window', '2h');
    fixture.componentRef.setInput('baseline', true);
    fixture.detectChanges();
    // No request value can be produced for a window the ladder refuses …
    expect(component.resourcesWindow()).toBeNull();
    // … and the body renders NO baseline surface at all.
    expect(fixture.nativeElement.querySelector('[data-test="metrics-baseline-disclosure"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="central-tab-baseline-refused"]')).toBeNull();
  });

  it('every value the mapping CAN produce is on the frozen ResourcesWindow union', () => {
    const ladder: readonly ResourcesWindow[] = ['15m', '1h', '24h', '7d'];
    for (const option of component.rangeOptions) {
      const mapped = component.resourcesWindow();
      if (mapped !== null) {
        expect(ladder).toContain(mapped);
      }
    }
  });
});

describe('ResourceTabComponent — the retention-tier copy (3.12)', () => {
  let fixture: ComponentFixture<ResourceTabComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResourceTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: ResourcesService, useValue: resourcesMock },
      ],
    }).compileComponents();
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [] }));
    resourcesMock.getLive.mockReturnValue(new Subject<LivePayload>().asObservable());
    resourcesMock.getSelfCost.mockReturnValue(new Subject<LivePayload>().asObservable());
    fixture = TestBed.createComponent(ResourceTabComponent);
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('names the retention TIERS — raw 48 h, minute 14 d, hour 180 d', () => {
    const help = fixture.nativeElement.querySelector(
      '[data-test="resource-range-retention-tiers"]',
    );
    expect(help).not.toBeNull();
    const text = help.textContent ?? '';
    expect(text).toContain('48 h');
    expect(text).toContain('14 d');
    expect(text).toContain('180 d');
    expect(text).toContain('retention tiers');
  });

  it('says the tiers BACK the ladder — they are not range options', () => {
    const help = fixture.nativeElement.querySelector(
      '[data-test="resource-range-retention-tiers"]',
    );
    expect((help.textContent ?? '')).toContain('not range options');
  });

  it('the TIERS are never offered as options', () => {
    const select = fixture.nativeElement.querySelector(
      '[data-test="resource-range-select"]',
    ) as HTMLSelectElement;
    const values = Array.from(select.options).map((option) => option.value);
    for (const tier of ['48h', '14d', '180d', '30s', '60s', '3600s']) {
      expect(values).not.toContain(tier);
    }
  });
});

// ═════════════════════════════════════════════════════════════════════
// PHASE 5 — the flat column, the window loads, the per-source state
// ═════════════════════════════════════════════════════════════════════

describe('ResourceTabComponent — the flat column (5.2 / 5.12 / IMPL-03)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResourceTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: ResourcesService, useValue: resourcesMock },
      ],
    }).compileComponents();
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [makeAnomaly('a')] }));
    resourcesMock.getLive.mockReturnValue(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeLive().self_cost));
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('renders the three sections IN ORDER: live strip → timeline panel → anomalies panel', async () => {
    const { fixture } = await createBody();
    const host = fixture.nativeElement as HTMLElement;
    const strip = host.querySelector('[data-test="resource-live-strip"]');
    const timeline = host.querySelector('[data-test="resource-timeline-panel"]');
    const anomalies = host.querySelector('[data-test="resource-anomalies-panel"]');
    expect(strip).not.toBeNull();
    expect(timeline).not.toBeNull();
    expect(anomalies).not.toBeNull();
    const following = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(strip!.compareDocumentPosition(timeline!) & following).toBeTruthy();
    expect(timeline!.compareDocumentPosition(anomalies!) & following).toBeTruthy();
    fixture.destroy();
  });

  it('the tab body carries NO nested tab group and NO placeholder (5.12)', async () => {
    const { fixture } = await createBody();
    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelectorAll('mat-tab-group').length).toBe(0);
    expect(host.querySelector('[data-test="resource-tab-placeholder"]')).toBeNull();
    expect(host.textContent).not.toContain('The resource module lands here');
    fixture.destroy();
  });

  it('the data lands: the live strip, the timeline chart and the anomaly rows all render', async () => {
    const { fixture } = await createBody();
    const host = fixture.nativeElement as HTMLElement;
    // The live strip shows the fetched snapshot (the 8 s poll's first tick).
    expect(host.querySelector('[data-test="resource-live-strip"]')?.textContent).toContain('1700');
    // The timeline chart host rendered through the registry.
    expect(
      host.querySelector('[data-test="resource-timeline-panel"] [data-test="timeline-chart"]'),
    ).not.toBeNull();
    // The anomalies panel rendered the row.
    expect(
      host.querySelector('[data-test="resource-anomalies-panel"] [data-test="anomaly-row-a"]'),
    ).not.toBeNull();
    fixture.destroy();
  });
});

describe('ResourceTabComponent — the window loads (5.4 / 5.11 / AM-16)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResourceTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: ResourcesService, useValue: resourcesMock },
      ],
    }).compileComponents();
    resourcesMock.getHistory.mockReset();
    resourcesMock.getAnomalies.mockReset();
    resourcesMock.getLive.mockReset();
    resourcesMock.getSelfCost.mockReset();
    jest.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    (console.warn as jest.Mock).mockRestore();
    jest.restoreAllMocks();
  });

  it('a range change issues EXACTLY ONE /history request — and NONE to /live or /self-cost (5.4)', async () => {
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [] }));
    resourcesMock.getLive.mockReturnValue(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeLive().self_cost));

    const { fixture } = await createBody();
    expect(resourcesMock.getHistory).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getHistory).toHaveBeenCalledWith('1h');
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getSelfCost).toHaveBeenCalledTimes(1);

    fixture.componentRef.setInput('window', '24h');
    fixture.detectChanges();
    fixture.detectChanges();

    // EXACTLY ONE more /history request, for the new window…
    expect(resourcesMock.getHistory).toHaveBeenCalledTimes(2);
    expect(resourcesMock.getHistory).toHaveBeenLastCalledWith('24h');
    // …and NONE to the live strip's endpoints.
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getSelfCost).toHaveBeenCalledTimes(1);
    fixture.destroy();
  });

  it('a window change DISCARDS an in-flight history response (5.11)', async () => {
    const historyA = new Subject<HistoryPayload>();
    resourcesMock.getHistory.mockReturnValueOnce(historyA.asObservable());
    resourcesMock.getAnomalies.mockReturnValueOnce(of({ anomalies: [] }));
    resourcesMock.getLive.mockReturnValue(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeLive().self_cost));

    const { fixture, component } = await createBody();
    expect(component.history()).toBeNull(); // A still in flight

    const historyB = makeHistory();
    resourcesMock.getHistory.mockReturnValueOnce(of(historyB));
    resourcesMock.getAnomalies.mockReturnValueOnce(of({ anomalies: [] }));
    fixture.componentRef.setInput('window', '24h');
    fixture.detectChanges();
    fixture.detectChanges();

    expect(component.history()).toBe(historyB); // B landed

    // A's slower response arrives LATE — it must never land.
    historyA.next(makeHistory({ truncated: true }));
    historyA.complete();
    fixture.detectChanges();
    expect(component.history()).toBe(historyB);
    fixture.destroy();
  });

  it('a window change DISCARDS an in-flight anomalies response (5.11)', async () => {
    const anomaliesA = new Subject<{ anomalies: Anomaly[] }>();
    resourcesMock.getHistory.mockReturnValueOnce(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValueOnce(anomaliesA.asObservable());
    resourcesMock.getLive.mockReturnValue(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeLive().self_cost));

    const { fixture, component } = await createBody();
    expect(component.anomalies()).toBeNull(); // A still in flight

    resourcesMock.getHistory.mockReturnValueOnce(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValueOnce(of({ anomalies: [makeAnomaly('b')] }));
    fixture.componentRef.setInput('window', '24h');
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.anomalies()).toEqual([makeAnomaly('b')]);

    anomaliesA.next({ anomalies: [makeAnomaly('stale')] });
    anomaliesA.complete();
    fixture.detectChanges();
    expect(component.anomalies()).toEqual([makeAnomaly('b')]); // the stale rows never landed
    fixture.destroy();
  });

  it('a window change does NOT discard the live strip\'s 8 s poll response (5.11)', async () => {
    const liveA = new Subject<LivePayload>();
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [] }));
    resourcesMock.getLive.mockReturnValue(liveA.asObservable());
    resourcesMock.getSelfCost.mockReturnValue(of(makeLive().self_cost));

    const { fixture } = await createBody();
    const host = fixture.nativeElement as HTMLElement;
    // The strip is still skeleton (the first live tick in flight).
    expect(host.querySelector('[data-test="resource-live-strip"]')?.textContent).not.toContain('1700');

    // A window change happens while the live tick is in flight.
    fixture.componentRef.setInput('window', '24h');
    fixture.detectChanges();
    fixture.detectChanges();
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1); // not re-polled by the window

    // The in-flight LIVE response resolves AFTER the window change — it LANDS.
    liveA.next(makeLive());
    liveA.complete();
    fixture.detectChanges();
    expect(host.querySelector('[data-test="resource-live-strip"]')?.textContent).toContain('1700');
    fixture.destroy();
  });

  it('a refused window (a central-only value) issues NO resources request at all (3.6)', async () => {
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [] }));

    const { fixture } = await createBody({ window: '2h' });
    expect(resourcesMock.getHistory).not.toHaveBeenCalled();
    expect(resourcesMock.getAnomalies).not.toHaveBeenCalled();
    fixture.destroy();
  });
});

describe('ResourceTabComponent — the PER-SOURCE state (AM-1 / 5.10)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResourceTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: ResourcesService, useValue: resourcesMock },
        {
          // The CENTRAL door — mocked refusing (the default install
          // posture). The body must never call it.
          provide: MetricsService,
          useValue: {
            getFamilies: jest.fn(() => { throw new Error('503 auth_not_configured'); }),
            getSnapshot: jest.fn(() => { throw new Error('503 auth_not_configured'); }),
            getSeries: jest.fn(() => { throw new Error('503 auth_not_configured'); }),
          },
        },
      ],
    }).compileComponents();
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of({ anomalies: [makeAnomaly('a')] }));
    resourcesMock.getLive.mockReturnValue(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeLive().self_cost));
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('with the central door refusing and the resources door answering 200, tab 1 renders real data and NO auth panel', async () => {
    const { fixture } = await createBody();
    const host = fixture.nativeElement as HTMLElement;

    // Real data, from the resources door only.
    expect(host.querySelector('[data-test="resource-live-strip"]')?.textContent).toContain('1700');
    expect(
      host.querySelector('[data-test="resource-timeline-panel"] [data-test="timeline-chart"]'),
    ).not.toBeNull();

    // The central door's failure copy NEVER reaches this body.
    expect(host.textContent).not.toContain('auth_not_configured');
    expect(host.querySelector('[data-test="central-tab-error"]')).toBeNull();
    expect(host.querySelector('[data-test="metrics-empty-panel"]')).toBeNull();

    // The central door's client was never called.
    const metrics = TestBed.inject(MetricsService) as unknown as {
      getFamilies: jest.Mock;
      getSnapshot: jest.Mock;
      getSeries: jest.Mock;
    };
    expect(metrics.getFamilies).not.toHaveBeenCalled();
    expect(metrics.getSnapshot).not.toHaveBeenCalled();
    expect(metrics.getSeries).not.toHaveBeenCalled();
    fixture.destroy();
  });

  it('the body injects ONLY the resources door (the structural pin)', () => {
    const src = readFileSync(join(__dirname, 'resource-tab.component.ts'), 'utf-8');
    expect(src).not.toContain('MetricsService');
    expect(src).not.toContain('/api/metrics');
  });
});
