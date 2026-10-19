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

/**
 * §2.8 degraded 200 shape — what the router emits on monitor
 * absent / disabled / first-tick / non-cgroup-v2 (D11).
 * `daemon` scalars and `cgroup` itself are `null`; `self_cost`
 * has `cpu_pct`/`threads` null but counts remain literal 0.
 * The FE must render with '—' placeholders, NOT throw during
 * change detection, NOT flip the single error branch.
 */
function makeDegradedLive(): LivePayload {
  return makeLive({
    daemon: { pid: null, cpu_pct: null, rss_mb: null, threads: null, fds: null, uptime_s: null },
    cgroup: null,
    self_cost: makeSelfCost({ cpu_pct: null, threads: null }),
  });
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

  // ── Error-banner anti-flicker (hardening pass) ──────────────────────

  it('hides the banner while an in-flight refresh runs with retained data, re-shows it on failure', () => {
    jest.useFakeTimers();
    startHappyPath();

    // Background failure → banner shows (data retained underneath).
    resourcesMock.getLive.mockReturnValueOnce(throwError(() => new Error('boom')));
    resourcesMock.getSelfCost.mockReturnValueOnce(throwError(() => new Error('boom')));
    jest.advanceTimersByTime(8000);
    fixture.detectChanges();
    expect(query('[data-test="live-error"]')).not.toBeNull();
    expect(query('[data-test="live-content"]')).not.toBeNull();

    // Next tick goes in flight with retained data → banner suppressed.
    const pendingLive = new Subject<LivePayload>();
    const pendingSelfCost = new Subject<SelfCostPayload>();
    resourcesMock.getLive.mockReturnValueOnce(pendingLive.asObservable());
    resourcesMock.getSelfCost.mockReturnValueOnce(pendingSelfCost.asObservable());
    jest.advanceTimersByTime(8000);
    fixture.detectChanges();
    expect(query('[data-test="live-error"]')).toBeNull(); // suppressed in flight
    expect(query('[data-test="live-skeleton"]')).toBeNull(); // never a skeleton flash
    expect(query('[data-test="live-content"]')).not.toBeNull(); // data retained

    // The in-flight tick FAILS → banner re-shows.
    pendingSelfCost.error(new Error('boom'));
    fixture.detectChanges();
    expect(query('[data-test="live-error"]')).not.toBeNull();
    expect(query('[data-test="live-content"]')).not.toBeNull();

    // A subsequent successful tick retires the banner.
    resourcesMock.getLive.mockReturnValueOnce(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValueOnce(of(makeSelfCost()));
    jest.advanceTimersByTime(8000);
    fixture.detectChanges();
    expect(query('[data-test="live-error"]')).toBeNull();
  });

  it('keeps the error branch during an in-flight retry when there is NO retained data', () => {
    jest.useFakeTimers();
    resourcesMock.getLive.mockReturnValueOnce(throwError(() => new Error('boom')));
    resourcesMock.getSelfCost.mockReturnValueOnce(throwError(() => new Error('boom')));
    fixture.detectChanges();

    expect(query('[data-test="live-error"]')).not.toBeNull();
    expect(query('[data-test="live-content"]')).toBeNull();

    // Retry with a slow response and NO data — the error branch stays up
    // (no skeleton flash, no empty content state).
    const pendingLive = new Subject<LivePayload>();
    resourcesMock.getLive.mockReturnValueOnce(pendingLive.asObservable());
    resourcesMock.getSelfCost.mockReturnValueOnce(new Subject<SelfCostPayload>().asObservable());
    query('[data-test="live-retry"]').nativeElement.click();
    fixture.detectChanges();

    expect(query('[data-test="live-error"]')).not.toBeNull();
    expect(query('[data-test="live-skeleton"]')).toBeNull();
    expect(query('[data-test="live-content"]')).toBeNull();
  });

  // ── Degraded-wire resilience (merge-blocking defect fix) ──────────
  //
  // The §2.8 /live endpoint is a 200 with a valid-but-degraded body
  // on three legitimate paths: monitor disabled, first-tick empty
  // ring, non-cgroup-v2 host (D11). The FE must render placeholders,
  // NOT throw during change detection, NOT flip the single error
  // branch, and NOT show the skeleton. Before the fix, the unguarded
  // `payload.cgroup.*` derefs + `fmt()` without null-safety threw a
  // TypeError and blanked the page.

  it('renders cards with placeholders when the wire is fully degraded (cgroup null + daemon scalars null)', () => {
    jest.useFakeTimers();
    resourcesMock.getLive.mockReturnValue(of(makeDegradedLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost({ cpu_pct: null, threads: null })));
    // The component must complete construction + first render
    // without throwing — the bug class is a TypeError during
    // change detection on `payload.cgroup.memory.current_mb`.
    expect(() => fixture.detectChanges()).not.toThrow();

    // No skeleton, no error — degraded 200 is NOT an error.
    expect(query('[data-test="live-skeleton"]')).toBeNull();
    expect(query('[data-test="live-error"]')).toBeNull();
    expect(query('[data-test="live-content"]')).not.toBeNull();

    // All four cards are still present (the user sees the page).
    expect(query('[data-test="stat-daemon"]')).not.toBeNull();
    expect(query('[data-test="stat-cgroup-memory"]')).not.toBeNull();
    expect(query('[data-test="stat-attribution"]')).not.toBeNull();
    expect(query('[data-test="stat-overruns"]')).not.toBeNull();
  });

  it('renders "—" in every null-bearing stat value place (daemon card)', () => {
    jest.useFakeTimers();
    resourcesMock.getLive.mockReturnValue(of(makeDegradedLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost({ cpu_pct: null, threads: null })));
    fixture.detectChanges();

    const daemonCard = query('[data-test="stat-daemon"]');
    expect(daemonCard).not.toBeNull();
    const daemonText = daemonCard.nativeElement.textContent as string;
    // Each null daemon scalar renders as '—'.
    expect(daemonText).toContain('—');
    // Specifically the typed test hooks used by the existing test suite.
    expect(query('[data-test="stat-daemon-cpu"]').nativeElement.textContent).toContain('—');
    expect(query('[data-test="stat-daemon-rss"]').nativeElement.textContent).toContain('—');
  });

  it('renders "—" in the cgroup card when the whole cgroup block is null', () => {
    jest.useFakeTimers();
    resourcesMock.getLive.mockReturnValue(of(makeDegradedLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost()));
    fixture.detectChanges();

    const cgroupCard = query('[data-test="stat-cgroup-memory"]');
    expect(cgroupCard).not.toBeNull();
    const cgroupText = cgroupCard.nativeElement.textContent as string;
    // Eight unguarded derefs (5 memory + 2 usec/1e6 + nr_throttled)
    // survive the null cgroup block — every value becomes '—'.
    // Card title stays visible so the user sees the slot exists.
    expect(cgroupText).toContain('Cgroup memory');
    expect((cgroupText.match(/—/g) ?? []).length).toBeGreaterThanOrEqual(8);
  });

  it('renders "—" for self-cost chip cpu_pct + threads when the wire omits them', () => {
    jest.useFakeTimers();
    resourcesMock.getLive.mockReturnValue(of(makeLive()));
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost({ cpu_pct: null, threads: null })));
    fixture.detectChanges();

    const chip = query('[data-test="self-cost-chip"]');
    expect(chip).not.toBeNull();
    const chipText = chip.nativeElement.textContent as string;
    // cpu_pct + threads both nullable; counts (samples/ticks_skipped)
    // remain literal numbers.
    expect(chipText).toContain('—');
    // The samples count is not nullable on the wire (literal 0) —
    // it must still render as a literal number, NOT '—'.
    expect(chipText).toContain('320');
  });

  it('survives a mid-tick degraded shape (first ring row only — daemon pid/threads null, cpu_pct/rss null)', () => {
    // §2.8 mid-tick degraded shape (one row only — the delta anchor
    // is missing for cpu_pct; pid/threads/fds/uptime are still null).
    // This is the exact shape `_empty_live_shape()` produces when
    // the monitor exists but the ring has not produced a row pair.
    const midTick = makeLive({
      daemon: { pid: null, cpu_pct: null, rss_mb: null, threads: null, fds: null, uptime_s: null },
      cgroup: null,
    });
    jest.useFakeTimers();
    resourcesMock.getLive.mockReturnValue(of(midTick));
    resourcesMock.getSelfCost.mockReturnValue(of(makeSelfCost()));
    expect(() => fixture.detectChanges()).not.toThrow();
    expect(query('[data-test="live-error"]')).toBeNull();
    expect(query('[data-test="live-skeleton"]')).toBeNull();
    expect(query('[data-test="live-content"]')).not.toBeNull();
  });

  it('background-tick to a degraded wire replaces the snapshot with placeholders (no error banner, no skeleton)', () => {
    jest.useFakeTimers();
    startHappyPath();
    // Sanity: the initial populated snapshot is what the user sees.
    expect(query('[data-test="stat-daemon-rss"]').nativeElement.textContent).toContain('1720');

    // Background tick returns the degraded shape. A degraded 200 is
    // a SUCCESSFUL tick — the new shape replaces the last snapshot
    // (placeholders where the wire is null), and the single error
    // branch stays DOWN. This contrasts with the transport-failure
    // path (a real throwError), which retains the last-known data
    // AND flips the banner.
    resourcesMock.getLive.mockReturnValueOnce(of(makeDegradedLive()));
    resourcesMock.getSelfCost.mockReturnValueOnce(of(makeSelfCost()));
    jest.advanceTimersByTime(8000);
    fixture.detectChanges();

    expect(query('[data-test="live-error"]')).toBeNull();
    expect(query('[data-test="live-skeleton"]')).toBeNull();
    expect(query('[data-test="live-content"]')).not.toBeNull();
    // The freshest tick's wire shape is what the user sees — a
    // degraded 200 supersedes the last-good populated snapshot.
    expect(query('[data-test="stat-daemon-rss"]').nativeElement.textContent).toContain('—');
  });
});
