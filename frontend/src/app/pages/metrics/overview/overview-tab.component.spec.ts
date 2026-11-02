// The OVERVIEW TAB spec (P5 task 5.5/5.8 · D1 pack `metrics-fe-page`).
//
// The polling discipline is the load-bearing surface:
//
//   * a NAMED `POLL_INTERVAL_MS` — never a bare literal;
//   * the poll starts only while the tab is the selected one;
//   * a SKIP-IF-IN-FLIGHT guard — a slow door never piles up ticks;
//   * MONOTONIC SUPERSEDE TOKENS — a slower EARLIER response can never
//     overwrite a newer one (P5-M3; inverting the check turns this RED);
//   * the teardown clears the interval (P5-M2);
//   * an unreachable door degrades to the LAST-GOOD view, MARKED STALE —
//     it never blanks and never polls harder.

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Observable, Subject } from 'rxjs';

import { MetricsService } from '../../../services/metrics.service';
import { MetricsSnapshotPayload } from '../../../models/metrics.model';
import { OverviewTabComponent } from './overview-tab.component';

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

function snapshotPayload(name: string, value: number): MetricsSnapshotPayload {
  return {
    ...envelope(),
    series: [
      {
        name,
        labels: { stat: 'p50' },
        kind: 'gauge',
        monotonic: false,
        value,
        last_publish_age_s: 1,
      },
    ],
    truncated: false,
  };
}

/** A stub service that records every call and lets the test emit. */
class StubMetricsService {
  readonly snapshotCalls: Subject<MetricsSnapshotPayload>[] = [];

  getSnapshot(): Observable<MetricsSnapshotPayload> {
    const subject = new Subject<MetricsSnapshotPayload>();
    this.snapshotCalls.push(subject);
    return subject.asObservable();
  }
}

describe('OverviewTabComponent — the polling discipline', () => {
  let fixture: ComponentFixture<OverviewTabComponent>;
  let component: OverviewTabComponent;
  let service: StubMetricsService;

  beforeEach(async () => {
    jest.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [OverviewTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    service = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(OverviewTabComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
    jest.useRealTimers();
  });

  it('declares the named poll interval (10 s)', () => {
    expect(component.POLL_INTERVAL_MS).toBe(10_000);
  });

  it('does NOT poll while the tab is not the selected one', () => {
    fixture.detectChanges();
    expect(service.snapshotCalls).toHaveLength(0);
  });

  it('polls once the tab becomes the selected one', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    expect(service.snapshotCalls).toHaveLength(1);
  });

  it('SKIPS a tick while the previous request is still in flight', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    expect(service.snapshotCalls).toHaveLength(1);
    jest.advanceTimersByTime(component.POLL_INTERVAL_MS * 3);
    expect(service.snapshotCalls).toHaveLength(1);
  });

  it('polls again once the previous request has landed', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.snapshotCalls[0].next(snapshotPayload('a', 1));
    jest.advanceTimersByTime(component.POLL_INTERVAL_MS);
    expect(service.snapshotCalls).toHaveLength(2);
  });

  it('STOPS polling when the tab is no longer the selected one', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    expect(service.snapshotCalls).toHaveLength(1);
    fixture.componentRef.setInput('active', false);
    fixture.detectChanges();
    jest.advanceTimersByTime(component.POLL_INTERVAL_MS * 3);
    expect(service.snapshotCalls).toHaveLength(1);
  });

  it('clears the interval on teardown (P5-M2)', () => {
    const clearSpy = jest.spyOn(globalThis, 'clearInterval');
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    expect(service.snapshotCalls).toHaveLength(1);
    fixture.destroy();
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });
});

describe('OverviewTabComponent — the monotonic supersede token (P5-M3)', () => {
  let fixture: ComponentFixture<OverviewTabComponent>;
  let component: OverviewTabComponent;
  let service: StubMetricsService;

  beforeEach(async () => {
    jest.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [OverviewTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    service = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(OverviewTabComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
    jest.useRealTimers();
  });

  it('a SLOWER EARLIER response never overwrites a newer one', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    // Two requests in flight: the poll tick and a user-initiated refresh.
    component.refresh();
    expect(service.snapshotCalls).toHaveLength(2);

    // The NEWER response lands first.
    service.snapshotCalls[1].next(snapshotPayload('newer', 20));
    expect(component.snapshot()?.series[0].name).toBe('newer');

    // The OLDER response lands afterwards — it must be discarded.
    service.snapshotCalls[0].next(snapshotPayload('older', 10));
    expect(component.snapshot()?.series[0].name).toBe('newer');
  });

  it('a superseded ERROR never replaces the newer good payload', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    component.refresh();

    service.snapshotCalls[1].next(snapshotPayload('newer', 20));
    service.snapshotCalls[0].error(new Error('boom'));

    expect(component.snapshot()?.series[0].name).toBe('newer');
    expect(component.error()).toBeNull();
    expect(component.stale()).toBe(false);
  });
});

describe('OverviewTabComponent — the degraded / stale view', () => {
  let fixture: ComponentFixture<OverviewTabComponent>;
  let component: OverviewTabComponent;
  let service: StubMetricsService;

  beforeEach(async () => {
    jest.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [OverviewTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
      ],
    }).compileComponents();
    service = TestBed.inject(MetricsService) as unknown as StubMetricsService;
    fixture = TestBed.createComponent(OverviewTabComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
    jest.useRealTimers();
  });

  it('keeps the LAST-GOOD data when the door becomes unreachable', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.snapshotCalls[0].next(snapshotPayload('kept', 7));
    expect(component.snapshot()?.series[0].name).toBe('kept');

    component.refresh();
    service.snapshotCalls[1].error(new Error('unreachable'));

    expect(component.snapshot()?.series[0].name).toBe('kept');
    expect(component.error()).not.toBeNull();
    expect(component.stale()).toBe(true);
  });

  it('renders the stat cards of the current values', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.snapshotCalls[0].next(snapshotPayload('kept', 7));
    fixture.detectChanges();

    const cards = component.cards();
    expect(cards).toHaveLength(1);
    expect(cards[0].name).toBe('kept');
    expect(cards[0].value).toBe('7');
    expect(cards[0].labels).toBe('stat=p50');
  });

  it('surfaces a bounded snapshot page — the W-4 truncated flag is never silent', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.snapshotCalls[0].next({
      ...snapshotPayload('kept', 7),
      truncated: true,
      next_cursor: 'opaque',
    });
    fixture.detectChanges();

    const meta = fixture.nativeElement.querySelector('[data-test="overview-meta"]');
    expect(meta).not.toBeNull();
    const text = (meta.textContent ?? '').toLowerCase();
    expect(text).toContain('bounded');
    // The data itself is still rendered (never blank, never wrong).
    expect(component.cards()).toHaveLength(1);
  });

  it('renders no bound note when the snapshot page is complete', () => {
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();
    service.snapshotCalls[0].next(snapshotPayload('kept', 7));
    fixture.detectChanges();

    const text = (
      fixture.nativeElement.querySelector('[data-test="overview-meta"]')?.textContent ?? ''
    ).toLowerCase();
    expect(text).not.toContain('bounded');
  });
});
