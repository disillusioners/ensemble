// The TIMELINE PANEL spec (T1 phase 5 — the panel is PRESENTATIONAL).
//
// Phase-5 disposition of the phase-1..4 legs (per-assertion, none
// dropped — see phase5-evidence.md for the full table):
//
//   * the window state, the window-picker UI and the window fetches
//     (selectWindow / loadTimeline / TIMELINE_WINDOWS / DEFAULT_TIMELINE_WINDOW)
//     MOVED to the tab body (5.4) — their assertions now live in
//     `tabs/resource-tab.component.spec.ts` (one history request per
//     range change, the generation supersede);
//   * the drill-down's "a window change closes the panel" leg is
//     DISPOSITIONED: in phase 5 the drill-down is WINDOW-INDEPENDENT
//     (5.4), so a window change neither fetches nor closes it;
//   * the RENDERER was RE-POINTED onto the Chart.js panel role (5.9 —
//     the AM-27 second re-point): the phase-4 "house SVG paths" leg was
//     re-specified to the panel proxies + hidden table, and the house
//     SVG is now asserted on the MOUNT-FALLBACK path (badge + fallback);
//   * everything else the panel still owns (series toggles, truncated
//     banner, the drill-down + its supersede token, the data-gap
//     strip, the skeleton/error branches driven by the body's inputs)
//     is asserted HERE.

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Subject, of, throwError } from 'rxjs';
import { readFileSync } from 'fs';
import { join } from 'path';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { ResourcesService } from '../../../../services/resources.service';
import {
  AnomaliesPayload,
  Anomaly,
  EvidencePayload,
  HistoryPayload,
  TracebackPayload,
} from '../../../../models/resources.models';
import { TimelineTabComponent } from './timeline-tab.component';

// ── Fixtures — naive-UTC wire strings (D10) ─────────────────────────

function makeHistory(overrides: Partial<HistoryPayload> = {}): HistoryPayload {
  return {
    points: [
      { t: '2026-10-18T16:00:00', daemon_cpu: 1, daemon_rss_mb: 1700, cgroup_mem_mb: 5800, threads: 40, instances_active: 5 },
      { t: '2026-10-18T16:15:00', daemon_cpu: 2, daemon_rss_mb: 1750, cgroup_mem_mb: 5850, threads: 42, instances_active: 5 },
      { t: '2026-10-18T16:30:00', daemon_cpu: 3, daemon_rss_mb: 1800, cgroup_mem_mb: 5900, threads: 44, instances_active: 6 },
      { t: '2026-10-18T16:45:00', daemon_cpu: 2, daemon_rss_mb: 1850, cgroup_mem_mb: 5950, threads: 44, instances_active: 5 },
      { t: '2026-10-18T17:00:00', daemon_cpu: 4, daemon_rss_mb: 1900, cgroup_mem_mb: 6000, threads: 46, instances_active: 5 },
    ],
    resolution_used: 'raw',
    rollup: 'none',
    truncated: false,
    ...overrides,
  };
}

function makeAnomaly(overrides: Partial<Anomaly> = {}): Anomaly {
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

function makeAnomalies(anomalies: Anomaly[] = [makeAnomaly()]): AnomaliesPayload {
  return { anomalies };
}

function makeEvidence(overrides: Partial<EvidencePayload> = {}): EvidencePayload {
  return {
    anomaly: makeAnomaly(),
    samples: [],
    top_pids_per_bucket: [],
    children_delta: [],
    contributors: [
      { instance_id: 'inst-1', agent_id: 'worker', share_pct: 62.5 },
      { instance_id: 'inst-2', agent_id: 'developer', share_pct: 20 },
      { share_pct: 17.5, unattributed: true },
    ],
    ...overrides,
  };
}

function makeTraceback(overrides: Partial<TracebackPayload> = {}): TracebackPayload {
  return {
    window: { t0: '2026-10-18T16:29:00.000Z', t1: '2026-10-18T16:41:00.000Z' },
    instances_alive: [{ instance_id: 'inst-1', agent: 'worker', status: 'running' }],
    in_flight_tasks: [{ work_id: 'work-9', status: 'PROCESSING' }],
    spawned_children: [
      { pid: 100, kind: 'chart_render', name: 'mmdc', instance_id: 'inst-1' },
      { pid: 101, kind: 'bash', name: 'python' },
    ],
    structural: { queue_depth: 3, workers: 2, checkpoints: 12, gc_events: 5 },
    findings: [],
    ...overrides,
  };
}

describe('TimelineTabComponent — the presentational panel (phase 5)', () => {
  let fixture: ComponentFixture<TimelineTabComponent>;
  let component: TimelineTabComponent;
  const resourcesMock: {
    getEvidence: jest.Mock;
    getTraceback: jest.Mock;
  } = {
    getEvidence: jest.fn(),
    getTraceback: jest.fn(),
  };

  function query(selector: string) {
    return fixture.debugElement.query(By.css(selector));
  }

  function queryAll(selector: string) {
    return fixture.debugElement.queryAll(By.css(selector));
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TimelineTabComponent],
      providers: [provideNoopAnimations(), { provide: ResourcesService, useValue: resourcesMock }],
    }).compileComponents();
    fixture = TestBed.createComponent(TimelineTabComponent);
    component = fixture.componentInstance;
  });

  beforeEach(() => {
    resourcesMock.getEvidence.mockReset();
    resourcesMock.getTraceback.mockReset();
    jest.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    (console.warn as jest.Mock).mockRestore();
    fixture.destroy();
  });

  /** Feed the panel the body-owned inputs (as the tab body does). */
  function feedData(
    history: HistoryPayload = makeHistory(),
    anomalies: readonly Anomaly[] = [makeAnomaly()],
  ): void {
    fixture.componentRef.setInput('history', history);
    fixture.componentRef.setInput('anomalies', anomalies);
    fixture.componentRef.setInput('loading', false);
    fixture.componentRef.setInput('error', null);
    fixture.detectChanges();
  }

  it('renders the skeleton ONLY from the body\'s loading input (5.4 — the body owns the fetch)', () => {
    fixture.componentRef.setInput('history', null);
    fixture.componentRef.setInput('loading', true);
    fixture.componentRef.setInput('error', null);
    fixture.detectChanges();
    expect(query('[data-test="timeline-skeleton"]')).not.toBeNull();
    expect(query('[data-test="timeline-chart"]')).toBeNull();

    // The first good fetch clears it — the body feeds the payload.
    feedData();
    expect(query('[data-test="timeline-skeleton"]')).toBeNull();
    expect(query('[data-test="timeline-chart"]')).not.toBeNull();
  });

  it('renders NO chart and NO skeleton when the body has neither data nor a load in flight', () => {
    // The transitional shell state: inputs untouched (loading defaults false).
    fixture.detectChanges();
    expect(query('[data-test="timeline-skeleton"]')).toBeNull();
    expect(query('[data-test="timeline-chart"]')).toBeNull();
  });

  it('renders through the CHART.JS PANEL role — the unit-class panels with proxies + the hidden table (5.9)', async () => {
    feedData();
    expect(query('[data-test="timeline-chart"]')).not.toBeNull();
    await fixture.whenStable();
    fixture.detectChanges();

    // The enabled series (memory both + CPU) yield TWO panels: memory + CPU.
    expect(query('[data-test="timeline-panel-memory"]')).not.toBeNull();
    expect(query('[data-test="timeline-panel-cpu"]')).not.toBeNull();
    expect(query('[data-test="timeline-panel-count"]')).toBeNull(); // counts disabled by default
    expect((query('[data-test="timeline-panel-title-memory"]').nativeElement.textContent ?? '').trim()).toBe('Memory (MB)');
    expect((query('[data-test="timeline-panel-title-cpu"]').nativeElement.textContent ?? '').trim()).toBe('CPU (%)');

    // The per-series DOM proxies — the wrapper's contract hooks.
    const proxies = queryAll('[data-test="chart-panel-proxy"]');
    const ids = proxies.map((proxy) => proxy.nativeElement.getAttribute('data-series'));
    expect(ids).toEqual(['daemon_rss_mb', 'cgroup_mem_mb', 'daemon_cpu']);
    for (const proxy of proxies) {
      expect(proxy.nativeElement.getAttribute('data-render-mode')).toBe('line');
    }
    // The visually-hidden data tables render the samples (the a11y
    // surface): 5 points × (2 memory series + 1 cpu series) = 15 rows
    // across the two panels' tables.
    const tables = queryAll('[data-test="chart-panel-data"]');
    expect(tables.length).toBe(2);
    const rows = tables.reduce(
      (total, table) => total + table.nativeElement.querySelectorAll('tbody tr').length,
      0,
    );
    expect(rows).toBe(15);
  });

  it('enabling a COUNT series adds the counts panel (the unit class travels with the series)', async () => {
    feedData();
    component.toggleSeries('threads');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(query('[data-test="timeline-panel-count"]')).not.toBeNull();
    expect((query('[data-test="timeline-panel-title-count"]').nativeElement.textContent ?? '').trim()).toBe('Counts');
  });

  it('the markers ride the house plugin with the VISIBLE label composed per anomaly (5.7)', () => {
    feedData();
    const memoryPanel = component.panels().find((panel) => panel.unitClass === 'memory');
    expect(memoryPanel).toBeDefined();
    const inputs = memoryPanel!.plan.inputs(component.panelData('memory'), component.panelHandlers('memory'));
    const anomalies = inputs['anomalies'] as readonly { anomalyId: string; title: string }[];
    expect(anomalies.length).toBe(1);
    expect(anomalies[0].anomalyId).toBe('anom-1');
    // The FOUR API words — visible, never hover-only (ADJ-iv shape 1).
    expect(anomalies[0].title).toContain('memory');
    expect(anomalies[0].title).toContain('severity high');
    expect(anomalies[0].title).toContain('series daemon_rss_mb');
    expect(anomalies[0].title).toContain('peak 1900');
  });

  it('renders the truncated banner strictly from the frozen payload flag (D9)', () => {
    feedData(makeHistory({ resolution_used: 'minute', truncated: true }));
    expect(query('[data-test="truncated-banner"]')).not.toBeNull();

    feedData(makeHistory({ truncated: false }));
    expect(query('[data-test="truncated-banner"]')).toBeNull();
  });

  it('shows exactly ONE error branch from the body\'s error input, and Retry emits to the owner', () => {
    feedData(); // last-good chart first
    fixture.componentRef.setInput('error', 'Unable to load resource history — showing the last known window.');
    fixture.detectChanges();

    expect(queryAll('[data-test="timeline-error"]').length).toBe(1);
    expect(query('[data-test="timeline-retry"]')).not.toBeNull();
    // The retained chart still renders underneath.
    expect(query('[data-test="timeline-chart"]')).not.toBeNull();

    // Retry hands back to the BODY (the fetch owner) — it never fetches itself.
    const retried: number[] = [];
    component.retry.subscribe(() => retried.push(1));
    query('[data-test="timeline-retry"]').nativeElement.click();
    fixture.detectChanges();
    expect(retried).toEqual([1]); // the panel holds no history fetcher — the source pin below
  });

  it('renders the data-gap chips derived from the payload, worded as a DATA GAP only (5.8)', () => {
    // daemon_cpu drops to 0 then regrows; cgroup_mem_mb goes null then regrows.
    const gapped = makeHistory({
      points: [
        { t: '2026-10-18T16:00:00', daemon_cpu: 1, daemon_rss_mb: 1700, cgroup_mem_mb: 5800, threads: 40, instances_active: 5 },
        { t: '2026-10-18T16:15:00', daemon_cpu: 0, daemon_rss_mb: 1750, cgroup_mem_mb: null, threads: 42, instances_active: 5 },
        { t: '2026-10-18T16:30:00', daemon_cpu: 3, daemon_rss_mb: 1800, cgroup_mem_mb: 5900, threads: 44, instances_active: 6 },
      ],
    });
    feedData(gapped);

    const chips = queryAll('[data-test="data-gap-chip"]');
    expect(chips.length).toBe(2);
    const text = chips.map((chip) => chip.nativeElement.textContent as string).join('\n');
    expect(text).toContain('DATA GAP — series unavailable in this interval');
    expect(/restart/i.test(text)).toBe(false);
    expect(/deploy/i.test(text)).toBe(false);
    expect(/promote/i.test(text)).toBe(false);
  });

  it('renders NO data-gap strip for an uninterrupted payload', () => {
    feedData();
    expect(query('[data-test="data-gaps"]')).toBeNull();
  });

  it('clicking an anomaly marker (the wrapper\'s pointClick) fetches evidence + traceback with the padded window (AC5)', () => {
    feedData();
    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));

    // The Chart.js panel's pointClick hand-off (5.9): the anomalyId rides
    // only when the click landed on a marker.
    component.onPanelPointClick({ anomalyId: 'anom-1', seriesId: 'daemon_rss_mb', tsMs: 1760790600000 });
    fixture.detectChanges();

    expect(resourcesMock.getEvidence).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getEvidence).toHaveBeenCalledWith('anom-1');
    // The pad rule window (max(60s, 10% span)) — the ported policy (4.2).
    expect(resourcesMock.getTraceback).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getTraceback).toHaveBeenCalledWith(
      '2026-10-18T16:29:00.000Z',
      '2026-10-18T16:41:00.000Z',
    );
  });

  it('openDrilldown(anomaly) — the anomalies panel\'s row-click path (5.5) — runs the SAME drill-down', () => {
    feedData();
    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));

    component.openDrilldown(makeAnomaly());
    fixture.detectChanges();

    expect(query('[data-test="drilldown-panel"]')).not.toBeNull();
    expect(resourcesMock.getEvidence).toHaveBeenCalledWith('anom-1');
    expect(resourcesMock.getTraceback).toHaveBeenCalledWith(
      '2026-10-18T16:29:00.000Z',
      '2026-10-18T16:41:00.000Z',
    );
  });

  it('renders the drill-down panel: anomaly summary, contributors, traceback children', () => {
    feedData();
    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));

    component.onPanelPointClick({ anomalyId: 'anom-1', seriesId: 'daemon_rss_mb', tsMs: 1760790600000 });
    fixture.detectChanges();

    const panel = query('[data-test="drilldown-panel"]');
    expect(panel).not.toBeNull();

    expect(query('[data-test="anomaly-kind"]').nativeElement.textContent).toContain('memory');
    expect(query('[data-test="anomaly-shape"]').nativeElement.textContent).toContain('ramp');
    expect(query('[data-test="anomaly-severity"]').nativeElement.textContent).toContain('high');
    expect(query('[data-test="anomaly-observed"]').nativeElement.textContent).toContain('1900');
    expect(query('[data-test="anomaly-baseline"]').nativeElement.textContent).toContain('1700');

    const contributors = query('[data-test="contributors"]').nativeElement.textContent as string;
    expect(contributors).toContain('62.5');
    expect(contributors).toContain('unattributed');

    const children = query('[data-test="traceback-children"]');
    expect(children.nativeElement.textContent).toContain('chart_render');
    const hot = children.queryAll(By.css('.child-row.hot'));
    expect(hot.length).toBe(1);
    expect(hot[0].nativeElement.getAttribute('data-hot')).toBe('true');

    expect(query('[data-test="traceback-alive"]').nativeElement.textContent).toContain('worker');
    expect(query('[data-test="traceback-inflight"]').nativeElement.textContent).toContain('work-9');
  });

  it('shows the drill-down error branch with retry on a failed drill-down fetch', () => {
    feedData();
    resourcesMock.getEvidence.mockReturnValue(throwError(() => new Error('boom')));
    resourcesMock.getTraceback.mockReturnValue(throwError(() => new Error('boom')));

    component.onPanelPointClick({ anomalyId: 'anom-1', seriesId: 'daemon_rss_mb', tsMs: 1760790600000 });
    fixture.detectChanges();

    expect(query('[data-test="drilldown-error"]')).not.toBeNull();
    expect(query('[data-test="drilldown-retry"]')).not.toBeNull();

    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));
    query('[data-test="drilldown-retry"]').nativeElement.click();
    fixture.detectChanges();

    expect(query('[data-test="drilldown-error"]')).toBeNull();
    expect(query('[data-test="contributors"]')).not.toBeNull();
  });

  it('toggles a series on and off (the panel owns the toggles)', async () => {
    feedData();
    await fixture.whenStable();
    fixture.detectChanges();
    const before = queryAll('[data-test="chart-panel-proxy"]').length;

    component.toggleSeries('threads');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(queryAll('[data-test="chart-panel-proxy"]').length).toBe(before + 1);

    component.toggleSeries('threads');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(queryAll('[data-test="chart-panel-proxy"]').length).toBe(before);
  });

  // ── Stale-response supersede (the drill-down keeps its own token) ──

  it('drops an out-of-order drill-down response — the newer marker click wins', () => {
    feedData();

    const evidenceA = new Subject<EvidencePayload>();
    const tracebackA = new Subject<TracebackPayload>();
    resourcesMock.getEvidence.mockReturnValueOnce(evidenceA.asObservable());
    resourcesMock.getTraceback.mockReturnValueOnce(tracebackA.asObservable());
    component.onPanelPointClick({ anomalyId: 'anom-1', seriesId: 'daemon_rss_mb', tsMs: 1760790600000 });
    fixture.detectChanges();
    expect(component.drilldownLoading()).toBe(true);
    expect(component.evidence()).toBeNull();

    const evidenceB = makeEvidence({
      contributors: [{ instance_id: 'inst-9', agent_id: 'leader', share_pct: 99 }],
    });
    resourcesMock.getEvidence.mockReturnValueOnce(of(evidenceB));
    resourcesMock.getTraceback.mockReturnValueOnce(of(makeTraceback()));
    component.onPanelPointClick({ anomalyId: 'anom-1', seriesId: 'daemon_rss_mb', tsMs: 1760790600000 });
    fixture.detectChanges();
    expect(component.drilldownLoading()).toBe(false);
    expect(component.evidence()).toBe(evidenceB);

    evidenceA.next(makeEvidence());
    evidenceA.complete();
    tracebackA.next(makeTraceback());
    tracebackA.complete();
    fixture.detectChanges();

    expect(component.drilldownError()).toBeNull();
    expect(component.evidence()).toBe(evidenceB);
    expect(query('[data-test="contributors"]').nativeElement.textContent).toContain('99');
  });

  it('a plain sample click carries NO anomalyId and opens NO drill-down', () => {
    feedData();
    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));

    component.onPanelPointClick({ seriesId: 'daemon_cpu', tsMs: 1760790600000 });
    fixture.detectChanges();

    expect(query('[data-test="drilldown-panel"]')).toBeNull();
    expect(resourcesMock.getEvidence).not.toHaveBeenCalled();
  });

  it('a MOUNT FAILURE drops the panel to the house SVG WITH A BADGE — and the fallback still drills down (5.9)', async () => {
    feedData();
    await fixture.whenStable();
    fixture.detectChanges();

    // The Chart.js mount fails on the memory panel — the runtime fallback.
    component.onMountFailed('memory', 'canvas unreachable');
    fixture.detectChanges();

    // The badge is SURFACED — never a silent downgrade.
    expect(queryAll('[data-test="panel-fallback-badge"]').length).toBe(1);
    const badge = query('[data-test="panel-fallback-badge"]').nativeElement.textContent as string;
    expect(badge).toContain('canvas unreachable');

    // The fallback renders the house SVG for the memory panel's series.
    const lines = queryAll('[data-test="house-series-line"]');
    expect(lines.length).toBeGreaterThanOrEqual(2); // the two memory series

    // The fallback's marker click still opens the drill-down (anomalyClick).
    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));
    const marker = query('[data-test="house-anomaly-marker"]');
    expect(marker).not.toBeNull();
    marker.triggerEventHandler('click', null);
    fixture.detectChanges();
    expect(query('[data-test="drilldown-panel"]')).not.toBeNull();
    expect(resourcesMock.getEvidence).toHaveBeenCalledWith('anom-1');
  });

  it('a late failed drill-down never flips the panel when a newer one already succeeded', () => {
    feedData();

    const evidenceA = new Subject<EvidencePayload>();
    const tracebackA = new Subject<TracebackPayload>();
    resourcesMock.getEvidence.mockReturnValueOnce(evidenceA.asObservable());
    resourcesMock.getTraceback.mockReturnValueOnce(tracebackA.asObservable());
    component.onPanelPointClick({ anomalyId: 'anom-1', seriesId: 'daemon_rss_mb', tsMs: 1760790600000 });
    fixture.detectChanges();

    resourcesMock.getEvidence.mockReturnValueOnce(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValueOnce(of(makeTraceback()));
    component.openDrilldown(makeAnomaly());
    fixture.detectChanges();
    expect(query('[data-test="drilldown-error"]')).toBeNull();

    evidenceA.error(new Error('late boom'));
    fixture.detectChanges();

    expect(query('[data-test="drilldown-error"]')).toBeNull();
    expect(component.evidence()).not.toBeNull();
  });
});

describe('TimelineTabComponent — the window-INDEPENDENCE pins (5.4)', () => {
  const src = readFileSync(
    join(__dirname, 'timeline-tab.component.ts'),
    'utf-8',
  );

  it('the panel carries NO window state and NO window fetch (the body owns them)', () => {
    expect(src).not.toContain('selectWindow');
    expect(src).not.toContain('TIMELINE_WINDOWS');
    expect(src).not.toContain('DEFAULT_TIMELINE_WINDOW');
    expect(src).not.toContain('getHistory');
    expect(src).not.toContain('getAnomalies');
    expect(src).not.toContain('window-picker');
    expect(src).not.toContain('mat-button-toggle');
  });

  it('the panel exposes openDrilldown for the anomalies row click (5.5)', () => {
    expect(src).toContain('openDrilldown(anomaly: Anomaly)');
  });
});
