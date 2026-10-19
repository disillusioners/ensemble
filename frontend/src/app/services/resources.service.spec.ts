import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { ResourcesService } from './resources.service';
import {
  AnomaliesPayload,
  EvidencePayload,
  HistoryPayload,
  LivePayload,
  SelfCostPayload,
  TracebackPayload,
} from '../models/resources.models';

// ── Fixtures — §2.8 shapes (minimal but contract-complete) ───────────

function makeSelfCost(overrides: Partial<SelfCostPayload> = {}): SelfCostPayload {
  return {
    cpu_pct: 0.42,
    cumulative_cpu_s: 12.5,
    buffers_bytes: 1_048_576,
    rss_budget_mb: 50,
    threads: 1,
    samples: 320,
    ticks_skipped: 2,
    flush: { batches: 5, bytes: 4096, errors: 0 },
    profiling: { active: false, capabilities: ['py_spy'] },
    ...overrides,
  };
}

function makeLive(overrides: Partial<LivePayload> = {}): LivePayload {
  return {
    ts: '2026-10-18T17:00:00',
    sample_level: 'full',
    daemon: { pid: 4242, cpu_pct: 3.1, rss_mb: 1720, threads: 44, fds: 34, uptime_s: 86400 },
    cgroup: {
      memory: { current_mb: 5900, peak_mb: 6331, anon_mb: 1900, file_mb: 3400, slab_mb: 350 },
      cpu: { usage_usec: 123456, throttled_usec: 100, nr_throttled: 3 },
    },
    children: [{ pid: 4243, kind: 'mcp', name: 'context7', cpu_pct: 0.1, rss_mb: 97 }],
    instances: [
      {
        instance_id: 'inst-1',
        agent: 'coder',
        estimated: true,
        charged_mb: 240,
        child_rss_mb: 12,
        children_n: 1,
        in_flight_tasks: 1,
      },
    ],
    attribution: { attributed_mb: 240, unattributed_mb: 60, coverage_pct: 80 },
    overruns: { count: 0, last_ts: null },
    self_cost: makeSelfCost(),
    ...overrides,
  };
}

function makeHistory(overrides: Partial<HistoryPayload> = {}): HistoryPayload {
  return {
    points: [
      {
        t: '2026-10-18T16:00:00',
        daemon_cpu: 2.5,
        daemon_rss_mb: 1700,
        cgroup_mem_mb: 5800,
        threads: 44,
        instances_active: 6,
      },
    ],
    resolution_used: 'minute',
    rollup: 'avg',
    truncated: false,
    ...overrides,
  };
}

function makeAnomalies(): AnomaliesPayload {
  return {
    anomalies: [
      {
        id: 'anom-1',
        kind: 'memory',
        t_start: '2026-10-18T15:30:00',
        t_end: '2026-10-18T15:45:00',
        peak: 8300,
        baseline: 1800,
        severity: 'high',
        series_key: 'daemon_rss_mb',
        shape: 'ramp',
      },
    ],
  };
}

function makeEvidence(): EvidencePayload {
  return {
    anomaly: makeAnomalies().anomalies[0],
    samples: [],
    top_pids_per_bucket: [],
    children_delta: [],
    contributors: [{ instance_id: 'inst-1', agent_id: 'designer', share_pct: 72 }],
  };
}

function makeTraceback(): TracebackPayload {
  return {
    window: { t0: '2026-10-18T15:20:00+00:00', t1: '2026-10-18T15:55:00+00:00' },
    instances_alive: [{ instance_id: 'inst-1', agent: 'designer', status: 'running' }],
    in_flight_tasks: [],
    spawned_children: [{ pid: 4243, kind: 'chart_render', name: 'chromium' }],
    structural: { queue_depth: 2, workers: 5, checkpoints: 100, gc_events: 3 },
    findings: [],
  };
}

describe('ResourcesService', () => {
  let service: ResourcesService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(ResourcesService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTesting.verify();
  });

  it('getLive() GETs /api/resources/live and returns the typed payload', () => {
    const payload = makeLive();
    let received: LivePayload | undefined;

    service.getLive().subscribe((p) => (received = p));

    const req = httpTesting.expectOne('/api/resources/live');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys().length).toBe(0);
    req.flush(payload);

    expect(received).toEqual(payload);
    expect(received?.daemon.pid).toBe(4242);
    expect(received?.attribution.coverage_pct).toBe(80);
  });

  it('getHistory() defaults resolution to auto and sends window+resolution', () => {
    let received: HistoryPayload | undefined;
    service.getHistory('1h').subscribe((p) => (received = p));

    const req = httpTesting.expectOne((r) => r.url === '/api/resources/history');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('window')).toBe('1h');
    expect(req.request.params.get('resolution')).toBe('auto');
    req.flush(makeHistory());

    expect(received?.resolution_used).toBe('minute');
  });

  it('getHistory() forwards an explicit resolution', () => {
    service.getHistory('7d', 'hour').subscribe();
    const req = httpTesting.expectOne((r) => r.url === '/api/resources/history');
    expect(req.request.params.get('window')).toBe('7d');
    expect(req.request.params.get('resolution')).toBe('hour');
    req.flush(makeHistory({ resolution_used: 'hour' }));
  });

  it('getAnomalies() sends no query params when called bare', () => {
    let received: AnomaliesPayload | undefined;
    service.getAnomalies().subscribe((p) => (received = p));

    const req = httpTesting.expectOne('/api/resources/anomalies');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush(makeAnomalies());

    expect(received?.anomalies.length).toBe(1);
    expect(received?.anomalies[0].shape).toBe('ramp');
  });

  it('getAnomalies() forwards window + limit when given', () => {
    service.getAnomalies('24h', 25).subscribe();
    const req = httpTesting.expectOne((r) => r.url === '/api/resources/anomalies');
    expect(req.request.params.get('window')).toBe('24h');
    expect(req.request.params.get('limit')).toBe('25');
    req.flush(makeAnomalies());
  });

  it('getEvidence() GETs the id-scoped evidence URL (id encoded)', () => {
    let received: EvidencePayload | undefined;
    service.getEvidence('anom-1 / weird').subscribe((p) => (received = p));

    const req = httpTesting.expectOne(
      '/api/resources/anomalies/anom-1%20%2F%20weird/evidence',
    );
    expect(req.request.method).toBe('GET');
    req.flush(makeEvidence());

    expect(received?.contributors[0].share_pct).toBe(72);
  });

  it('getTraceback() forwards t0/t1 as query params and round-trips them', () => {
    let received: TracebackPayload | undefined;
    service
      .getTraceback('2026-10-18T15:20:00+00:00', '2026-10-18T15:55:00+00:00')
      .subscribe((p) => (received = p));

    const req = httpTesting.expectOne((r) => r.url === '/api/resources/traceback');
    expect(req.request.params.get('t0')).toBe('2026-10-18T15:20:00+00:00');
    expect(req.request.params.get('t1')).toBe('2026-10-18T15:55:00+00:00');
    req.flush(makeTraceback());

    expect(received?.spawned_children[0].kind).toBe('chart_render');
  });

  it('getSelfCost() GETs /api/resources/self-cost', () => {
    let received: SelfCostPayload | undefined;
    service.getSelfCost().subscribe((p) => (received = p));

    const req = httpTesting.expectOne('/api/resources/self-cost');
    expect(req.request.method).toBe('GET');
    req.flush(makeSelfCost());

    expect(received?.buffers_bytes).toBe(1_048_576);
    expect(received?.ticks_skipped).toBe(2);
  });
});
