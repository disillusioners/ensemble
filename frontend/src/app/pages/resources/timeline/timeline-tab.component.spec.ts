import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Subject, of, throwError } from 'rxjs';
import { ResourcesService } from '../../../services/resources.service';
import {
  AnomaliesPayload,
  Anomaly,
  EvidencePayload,
  HistoryPayload,
  TracebackPayload,
} from '../../../models/resources.models';
import {
  DEFAULT_TIMELINE_WINDOW,
  TimelineTabComponent,
} from './timeline-tab.component';

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

describe('TimelineTabComponent', () => {
  let fixture: ComponentFixture<TimelineTabComponent>;
  let component: TimelineTabComponent;
  const resourcesMock: {
    getHistory: jest.Mock;
    getAnomalies: jest.Mock;
    getEvidence: jest.Mock;
    getTraceback: jest.Mock;
  } = {
    getHistory: jest.fn(),
    getAnomalies: jest.fn(),
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
      providers: [{ provide: ResourcesService, useValue: resourcesMock }],
    }).compileComponents();
    fixture = TestBed.createComponent(TimelineTabComponent);
    component = fixture.componentInstance;
  });

  beforeEach(() => {
    resourcesMock.getHistory.mockReset();
    resourcesMock.getAnomalies.mockReset();
    resourcesMock.getEvidence.mockReset();
    resourcesMock.getTraceback.mockReset();
    jest.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    (console.warn as jest.Mock).mockRestore();
  });

  function startHappyPath(): void {
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of(makeAnomalies()));
    fixture.detectChanges(); // ngOnInit → loadTimeline
  }

  it('defaults to the 1h window and fetches history + anomalies with it', () => {
    expect(DEFAULT_TIMELINE_WINDOW).toBe('1h');
    startHappyPath();

    expect(resourcesMock.getHistory).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getHistory).toHaveBeenCalledWith('1h');
    expect(resourcesMock.getAnomalies).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getAnomalies).toHaveBeenCalledWith('1h');
  });

  it('renders the svg-timeline with non-empty series paths', () => {
    startHappyPath();

    expect(query('[data-test="timeline-chart"]')).not.toBeNull();
    const lines = queryAll('[data-test="series-line"]');
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      expect((line.nativeElement.getAttribute('d') ?? '').length).toBeGreaterThan(0);
    }
  });

  it('renders the truncated banner strictly from the frozen payload flag (D9)', () => {
    resourcesMock.getHistory.mockReturnValue(
      of(makeHistory({ resolution_used: 'minute', truncated: true })),
    );
    resourcesMock.getAnomalies.mockReturnValue(of(makeAnomalies()));
    fixture.detectChanges();

    expect(query('[data-test="truncated-banner"]')).not.toBeNull();

    // A non-truncated payload hides it — no FE fallback logic.
    resourcesMock.getHistory.mockReturnValue(of(makeHistory({ truncated: false })));
    resourcesMock.getAnomalies.mockReturnValue(of(makeAnomalies()));
    component.selectWindow('24h');
    fixture.detectChanges();

    expect(query('[data-test="truncated-banner"]')).toBeNull();
  });

  it('re-fetches both series on a window change', () => {
    startHappyPath();
    expect(resourcesMock.getHistory).toHaveBeenCalledTimes(1);

    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of(makeAnomalies()));
    component.selectWindow('24h');
    fixture.detectChanges();

    expect(resourcesMock.getHistory).toHaveBeenCalledTimes(2);
    expect(resourcesMock.getHistory).toHaveBeenLastCalledWith('24h');
    expect(resourcesMock.getAnomalies).toHaveBeenLastCalledWith('24h');
  });

  it('does not re-fetch when selecting the already-active window', () => {
    startHappyPath();
    component.selectWindow('1h');
    expect(resourcesMock.getHistory).toHaveBeenCalledTimes(1);
  });

  it('shows the skeleton only before the first successful fetch', () => {
    const pending = new Subject<HistoryPayload>();
    resourcesMock.getHistory.mockReturnValue(pending.asObservable());
    resourcesMock.getAnomalies.mockReturnValue(of(makeAnomalies()));

    fixture.detectChanges();
    expect(query('[data-test="timeline-skeleton"]')).not.toBeNull();
    expect(query('[data-test="timeline-chart"]')).toBeNull();

    pending.next(makeHistory());
    pending.complete(); // forkJoin emits only when every source completes
    fixture.detectChanges();

    expect(query('[data-test="timeline-skeleton"]')).toBeNull();
    expect(query('[data-test="timeline-chart"]')).not.toBeNull();

    // A background window change must NOT re-flash the skeleton.
    resourcesMock.getHistory.mockReturnValue(new Subject<HistoryPayload>().asObservable());
    resourcesMock.getAnomalies.mockReturnValue(new Subject<AnomaliesPayload>().asObservable());
    component.selectWindow('24h');
    fixture.detectChanges();

    expect(query('[data-test="timeline-skeleton"]')).toBeNull();
    expect(query('[data-test="timeline-chart"]')).not.toBeNull();
  });

  it('retains last-good data and shows exactly one error branch with retry', () => {
    startHappyPath();

    resourcesMock.getHistory.mockReturnValue(throwError(() => new Error('boom')));
    resourcesMock.getAnomalies.mockReturnValue(throwError(() => new Error('boom')));
    component.selectWindow('24h');
    fixture.detectChanges();

    expect(queryAll('[data-test="timeline-error"]').length).toBe(1);
    expect(query('[data-test="timeline-retry"]')).not.toBeNull();
    // Retained chart still renders underneath.
    expect(query('[data-test="timeline-chart"]')).not.toBeNull();

    // Retry re-kicks the load.
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of(makeAnomalies()));
    query('[data-test="timeline-retry"]').nativeElement.click();
    fixture.detectChanges();

    expect(query('[data-test="timeline-error"]')).toBeNull();
    expect(query('[data-test="timeline-chart"]')).not.toBeNull();
  });

  it('clicking an anomaly marker fetches evidence + traceback with the padded window (AC5)', () => {
    startHappyPath();

    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));

    const marker = query('[data-test="anomaly-marker"]');
    expect(marker).not.toBeNull();
    marker.triggerEventHandler('click', null);
    fixture.detectChanges();

    // Exactly one evidence call for the clicked anomaly id.
    expect(resourcesMock.getEvidence).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getEvidence).toHaveBeenCalledWith('anom-1');
    // Exactly one traceback call, with the pad rule window (max(60s, 10% span)).
    expect(resourcesMock.getTraceback).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getTraceback).toHaveBeenCalledWith(
      '2026-10-18T16:29:00.000Z',
      '2026-10-18T16:41:00.000Z',
    );
  });

  it('renders the drill-down panel: anomaly summary, contributors, traceback children', () => {
    startHappyPath();
    resourcesMock.getEvidence.mockReturnValue(of(makeEvidence()));
    resourcesMock.getTraceback.mockReturnValue(of(makeTraceback()));

    query('[data-test="anomaly-marker"]').triggerEventHandler('click', null);
    fixture.detectChanges();

    const panel = query('[data-test="drilldown-panel"]');
    expect(panel).not.toBeNull();

    // Anomaly summary: kind / shape / severity / observed vs baseline.
    expect(query('[data-test="anomaly-kind"]').nativeElement.textContent).toContain('memory');
    expect(query('[data-test="anomaly-shape"]').nativeElement.textContent).toContain('ramp');
    expect(query('[data-test="anomaly-severity"]').nativeElement.textContent).toContain('high');
    expect(query('[data-test="anomaly-observed"]').nativeElement.textContent).toContain('1900');
    expect(query('[data-test="anomaly-baseline"]').nativeElement.textContent).toContain('1700');

    // Contributors with share_pct + the unattributed honesty row.
    const contributors = query('[data-test="contributors"]').nativeElement.textContent as string;
    expect(contributors).toContain('62.5');
    expect(contributors).toContain('unattributed');

    // Traceback children, incl. the chart_render hot signal.
    const children = query('[data-test="traceback-children"]');
    expect(children.nativeElement.textContent).toContain('chart_render');
    const hot = children.queryAll(By.css('.child-row.hot'));
    expect(hot.length).toBe(1);
    expect(hot[0].nativeElement.getAttribute('data-hot')).toBe('true');

    // Alive instances + in-flight tasks surfaced.
    expect(query('[data-test="traceback-alive"]').nativeElement.textContent).toContain('worker');
    expect(query('[data-test="traceback-inflight"]').nativeElement.textContent).toContain('work-9');
  });

  it('shows the drill-down error branch with retry on a failed drill-down fetch', () => {
    startHappyPath();
    resourcesMock.getEvidence.mockReturnValue(throwError(() => new Error('boom')));
    resourcesMock.getTraceback.mockReturnValue(throwError(() => new Error('boom')));

    query('[data-test="anomaly-marker"]').triggerEventHandler('click', null);
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

  it('toggles a series on and off (parent owns series state)', () => {
    startHappyPath();
    const before = queryAll('[data-test="series-line"]').length;

    component.toggleSeries('threads');
    fixture.detectChanges();
    expect(queryAll('[data-test="series-line"]').length).toBe(before + 1);

    component.toggleSeries('threads');
    fixture.detectChanges();
    expect(queryAll('[data-test="series-line"]').length).toBe(before);
  });

  // ── Stale-response supersede (hardening pass) ──────────────────────

  it('drops an out-of-order window response — the newer window wins', () => {
    // Window A ('1h' via ngOnInit) hangs in flight.
    const historyA = new Subject<HistoryPayload>();
    const anomaliesA = new Subject<AnomaliesPayload>();
    resourcesMock.getHistory.mockReturnValueOnce(historyA.asObservable());
    resourcesMock.getAnomalies.mockReturnValueOnce(anomaliesA.asObservable());
    fixture.detectChanges(); // ngOnInit → loadTimeline('1h')

    // Switch to window B — resolves immediately.
    const historyB = makeHistory({ resolution_used: 'minute', truncated: true });
    resourcesMock.getHistory.mockReturnValueOnce(of(historyB));
    resourcesMock.getAnomalies.mockReturnValueOnce(of(makeAnomalies()));
    component.selectWindow('24h');
    fixture.detectChanges();

    expect(resourcesMock.getHistory).toHaveBeenCalledTimes(2);
    expect(resourcesMock.getHistory).toHaveBeenLastCalledWith('24h');
    expect(component.history()).toBe(historyB); // B landed
    expect(query('[data-test="truncated-banner"]')).not.toBeNull();

    // A's slower response arrives LATE — it must be discarded.
    historyA.next(makeHistory({ truncated: false }));
    historyA.complete();
    anomaliesA.next(makeAnomalies());
    anomaliesA.complete();
    fixture.detectChanges();

    // UI/state still reflects B — A's stale payload never landed.
    expect(component.window()).toBe('24h');
    expect(component.history()).toBe(historyB);
    expect(query('[data-test="truncated-banner"]')).not.toBeNull();
  });

  it('drops an out-of-order window ERROR — a stale failure never flips the banner', () => {
    const historyA = new Subject<HistoryPayload>();
    const anomaliesA = new Subject<AnomaliesPayload>();
    resourcesMock.getHistory.mockReturnValueOnce(historyA.asObservable());
    resourcesMock.getAnomalies.mockReturnValueOnce(anomaliesA.asObservable());
    fixture.detectChanges(); // window A in flight

    const historyB = makeHistory();
    resourcesMock.getHistory.mockReturnValueOnce(of(historyB));
    resourcesMock.getAnomalies.mockReturnValueOnce(of(makeAnomalies()));
    component.selectWindow('24h');
    fixture.detectChanges();
    expect(query('[data-test="timeline-error"]')).toBeNull();

    // A fails late, AFTER B already succeeded — no stale error branch.
    historyA.error(new Error('late boom'));
    fixture.detectChanges();

    expect(query('[data-test="timeline-error"]')).toBeNull();
    expect(component.history()).toBe(historyB);
  });

  it('drops an out-of-order drill-down response — the newer marker click wins', () => {
    startHappyPath();

    // First marker click hangs in flight.
    const evidenceA = new Subject<EvidencePayload>();
    const tracebackA = new Subject<TracebackPayload>();
    resourcesMock.getEvidence.mockReturnValueOnce(evidenceA.asObservable());
    resourcesMock.getTraceback.mockReturnValueOnce(tracebackA.asObservable());
    query('[data-test="anomaly-marker"]').triggerEventHandler('click', null);
    fixture.detectChanges();
    expect(component.drilldownLoading()).toBe(true);
    expect(component.evidence()).toBeNull();

    // Second click on the marker supersedes it and resolves immediately.
    const evidenceB = makeEvidence({
      contributors: [{ instance_id: 'inst-9', agent_id: 'leader', share_pct: 99 }],
    });
    resourcesMock.getEvidence.mockReturnValueOnce(of(evidenceB));
    resourcesMock.getTraceback.mockReturnValueOnce(of(makeTraceback()));
    query('[data-test="anomaly-marker"]').triggerEventHandler('click', null);
    fixture.detectChanges();
    expect(component.drilldownLoading()).toBe(false);
    expect(component.evidence()).toBe(evidenceB);

    // A's slower drill-down arrives LATE — discarded.
    evidenceA.next(makeEvidence());
    evidenceA.complete();
    tracebackA.next(makeTraceback());
    tracebackA.complete();
    fixture.detectChanges();

    expect(component.drilldownError()).toBeNull();
    expect(component.evidence()).toBe(evidenceB);
    expect(query('[data-test="contributors"]').nativeElement.textContent).toContain('99');
  });

  it('drops an in-flight drill-down response after a window change closes the panel', () => {
    startHappyPath();

    const evidenceA = new Subject<EvidencePayload>();
    const tracebackA = new Subject<TracebackPayload>();
    resourcesMock.getEvidence.mockReturnValueOnce(evidenceA.asObservable());
    resourcesMock.getTraceback.mockReturnValueOnce(tracebackA.asObservable());
    query('[data-test="anomaly-marker"]').triggerEventHandler('click', null);
    fixture.detectChanges();
    expect(query('[data-test="drilldown-panel"]')).not.toBeNull();

    // Window change closes the drill-down while its fetch is in flight.
    resourcesMock.getHistory.mockReturnValue(of(makeHistory()));
    resourcesMock.getAnomalies.mockReturnValue(of(makeAnomalies()));
    component.selectWindow('24h');
    fixture.detectChanges();
    expect(query('[data-test="drilldown-panel"]')).toBeNull();

    // The late drill-down response must NOT repopulate the closed panel.
    evidenceA.next(makeEvidence());
    evidenceA.complete();
    tracebackA.next(makeTraceback());
    tracebackA.complete();
    fixture.detectChanges();

    expect(query('[data-test="drilldown-panel"]')).toBeNull();
    expect(component.evidence()).toBeNull();
    expect(component.traceback()).toBeNull();
  });
});
