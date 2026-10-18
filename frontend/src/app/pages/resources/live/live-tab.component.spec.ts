import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Subject, of, throwError } from 'rxjs';
import { ResourcesService } from '../../../services/resources.service';
import {
  LivePayload,
  SelfCostPayload,
} from '../../../models/resources.models';
import { LiveTabComponent } from './live-tab.component';

// ── Fixtures ────────────────────────────────────────────────────────

function makeSelfCost(overrides: Partial<SelfCostPayload> = {}): SelfCostPayload {
  return {
    cpu_pct: 0.42,
    cumulative_cpu_s: 12.5,
    buffers_bytes: 2_097_152, // 2 MB
    rss_budget_mb: 50,
    threads: 1,
    samples: 320,
    ticks_skipped: 2,
    flush: { batches: 5, bytes: 4096, errors: 0 },
    profiling: { active: false, capabilities: [] },
    ...overrides,
  };
}

function makeLive(overrides: Partial<LivePayload> = {}): LivePayload {
  return {
    ts: '2026-10-18T17:00:00',
    sample_level: 'full',
    daemon: { pid: 4242, cpu_pct: 3.1, rss_mb: 1720, threads: 44, fds: 34, uptime_s: 90_000 },
    cgroup: {
      memory: { current_mb: 5900, peak_mb: 6331, anon_mb: 1900, file_mb: 3400, slab_mb: 350 },
      cpu: { usage_usec: 123_456_789, throttled_usec: 100, nr_throttled: 3 },
    },
    children: [],
    instances: [],
    attribution: { attributed_mb: 240, unattributed_mb: 60, coverage_pct: 80 },
    overruns: { count: 0, last_ts: null },
    self_cost: makeSelfCost(),
    ...overrides,
  };
}

// ── Harness ─────────────────────────────────────────────────────────

describe('LiveTabComponent', () => {
  let fixture: ComponentFixture<LiveTabComponent>;
  let component: LiveTabComponent;
  const resourcesMock: {
    getLive: jest.Mock;
    getSelfCost: jest.Mock;
  } = { getLive: jest.fn(), getSelfCost: jest.fn() };

  function query(selector: string) {
    return fixture.debugElement.query(By.css(selector));
  }

  function queryAll(selector: string) {
    return fixture.debugElement.queryAll(By.css(selector));
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LiveTabComponent],
      providers: [{ provide: ResourcesService, useValue: resourcesMock }],
    }).compileComponents();
    fixture = TestBed.createComponent(LiveTabComponent);
    component = fixture.componentInstance;
  });

  beforeEach(() => {
    resourcesMock.getLive.mockReset();
    resourcesMock.getSelfCost.mockReset();
    jest.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.useRealTimers();
    (console.warn as jest.Mock).mockRestore();
  });

  /** Wire happy-path mocks + run the first tick. */
  function startHappyPath(): void {
    resourcesMock.getLive.mockReturnValue(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost()));
    fixture.detectChanges(); // triggers ngOnInit → first fetch
  }

  it('uses the 8 s poll interval (D6)', () => {
    expect(component.POLL_INTERVAL_MS).toBe(8000);
  });

  it('fetches on init then every 8 s', () => {
    jest.useFakeTimers();
    startHappyPath();

    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1);
    expect(resourcesMock.getSelfCost).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(8000);
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(2);

    jest.advanceTimersByTime(16_000);
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(4);

    // A sub-interval advance does NOT fire early.
    jest.advanceTimersByTime(7000);
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(4);
  });

  it('clears the interval on destroy (no polling after teardown)', () => {
    jest.useFakeTimers();
    startHappyPath();
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1);

    fixture.destroy();
    jest.advanceTimersByTime(80_000);

    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1);
  });

  it('renders the skeleton only before the first successful fetch', () => {
    jest.useFakeTimers();
    const liveSubject = new Subject<LivePayload>();
    resourcesMock.getLive.mockReturnValue(liveSubject.asObservable());
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost()));

    fixture.detectChanges(); // first fetch in flight

    expect(query('[data-test="live-skeleton"]')).not.toBeNull();
    expect(query('[data-test="live-content"]')).toBeNull();
    expect(query('[data-test="live-error"]')).toBeNull();

    liveSubject.next(makeLive());
    liveSubject.complete(); // forkJoin emits only when every source completes
    fixture.detectChanges();

    expect(query('[data-test="live-skeleton"]')).toBeNull();
    expect(query('[data-test="live-content"]')).not.toBeNull();

    // Background refresh (pending subject again) must NOT re-flash the skeleton.
    const secondSubject = new Subject<LivePayload>();
    resourcesMock.getLive.mockReturnValue(secondSubject.asObservable());
    resourcesMock.getSelfCost.mockReturnValue(new Subject<SelfCostPayload>().asObservable());
    jest.advanceTimersByTime(8000);
    fixture.detectChanges();

    expect(query('[data-test="live-content"]')).not.toBeNull();
    expect(query('[data-test="live-skeleton"]')).toBeNull();
  });

  it('retains last-good data and shows exactly one error branch on a background failure', () => {
    jest.useFakeTimers();
    startHappyPath();

    resourcesMock.getLive.mockReturnValueOnce(throwError(() => new Error('boom')));
    resourcesMock.getSelfCost.mockReturnValueOnce(throwError(() => new Error('boom')));
    jest.advanceTimersByTime(8000);
    fixture.detectChanges();

    expect(queryAll('[data-test="live-error"]').length).toBe(1);
    expect(query('[data-test="live-retry"]')).not.toBeNull();
    // Retained last-good snapshot still renders underneath.
    expect(query('[data-test="live-content"]')).not.toBeNull();
    expect(query('[data-test="stat-daemon-rss"]').nativeElement.textContent).toContain(
      '1720',
    );
    expect(query('[data-test="live-skeleton"]')).toBeNull();
  });

  it('shows the error branch (and no content) when the FIRST fetch fails', () => {
    jest.useFakeTimers();
    resourcesMock.getLive.mockReturnValue(throwError(() => new Error('boom')));
    resourcesMock.getSelfCost.mockReturnValue(throwError(() => new Error('boom')));

    fixture.detectChanges();

    expect(query('[data-test="live-error"]')).not.toBeNull();
    expect(query('[data-test="live-content"]')).toBeNull();
    expect(query('[data-test="live-skeleton"]')).toBeNull();
  });

  it('retry re-kicks the tick immediately', () => {
    jest.useFakeTimers();
    startHappyPath();

    resourcesMock.getLive.mockReturnValueOnce(throwError(() => new Error('boom')));
    resourcesMock.getSelfCost.mockReturnValueOnce(throwError(() => new Error('boom')));
    jest.advanceTimersByTime(8000);
    fixture.detectChanges();
    const callsAfterFailure = resourcesMock.getLive.mock.calls.length;

    resourcesMock.getLive.mockReturnValueOnce(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValueOnce(of(makeSelfCost()));
    query('[data-test="live-retry"]').nativeElement.click();
    fixture.detectChanges();

    expect(resourcesMock.getLive).toHaveBeenCalledTimes(callsAfterFailure + 1);
    expect(query('[data-test="live-error"]')).toBeNull();
    expect(query('[data-test="live-content"]')).not.toBeNull();
  });

  it('skips a tick while the previous fetch is still in flight (never queued)', () => {
    jest.useFakeTimers();
    const pending = new Subject<LivePayload>();
    resourcesMock.getLive.mockReturnValue(pending.asObservable());
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost()));

    fixture.detectChanges(); // first fetch in flight
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(8000);
    jest.advanceTimersByTime(8000);
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(1); // skipped, not piled up

    pending.next(makeLive()); // release the in-flight tick
    pending.complete(); // forkJoin emits only when every source completes
    fixture.detectChanges();
    expect(query('[data-test="live-content"]')).not.toBeNull();

    jest.advanceTimersByTime(8000); // the NEXT tick fires again
    expect(resourcesMock.getLive).toHaveBeenCalledTimes(2);
  });

  it('renders the self-cost chip fields (AC6 — display only, no budget math)', () => {
    jest.useFakeTimers();
    startHappyPath();

    const chip = query('[data-test="self-cost-chip"]');
    expect(chip).not.toBeNull();
    const text = chip.nativeElement.textContent as string;
    expect(text).toContain('0.42%'); // cpu_pct
    expect(text).toContain('12.5s'); // cumulative_cpu_s
    expect(text).toContain('2.0'); // buffers_bytes → MB
    expect(text).toContain('320'); // samples
    expect(text).toContain('2'); // ticks_skipped
  });

  it('renders the daemon / attribution / overrun stat cards', () => {
    jest.useFakeTimers();
    startHappyPath();

    expect(query('[data-test="stat-daemon"]')).not.toBeNull();
    expect(query('[data-test="stat-daemon-cpu"]').nativeElement.textContent).toContain(
      '3.1%',
    );
    expect(query('[data-test="stat-attribution"]')).not.toBeNull();
    expect(query('[data-test="stat-coverage"]').nativeElement.textContent).toContain('80%');
    expect(query('[data-test="stat-overruns"]').nativeElement.textContent).toContain(
      'never',
    );
  });
});
