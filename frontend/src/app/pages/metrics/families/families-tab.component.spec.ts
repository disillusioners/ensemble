// The FAMILIES TAB spec (P5 task 5.4/5.7/5.8 · D1 pack `metrics-fe-page`).
//
// The load-bearing pin: a DARK family shows WHY it is dark — the
// honest signal is the reason, never a fabricated zero. An unknown
// reason renders as `unknown(<value>)` (AM-7) — the FE never invents a
// meaning for a reason it does not know and never hides it.

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { MetricsFamily } from '../../../models/metrics.model';
import { FamiliesTabComponent } from './families-tab.component';

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

describe('FamiliesTabComponent — the availability rendering', () => {
  let fixture: ComponentFixture<FamiliesTabComponent>;
  let component: FamiliesTabComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [FamiliesTabComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();
    fixture = TestBed.createComponent(FamiliesTabComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('counts the live and the dark families', () => {
    fixture.componentRef.setInput('families', [
      family({ id: 'F1' }),
      family({
        id: 'F6',
        live_series_count: 0,
        availability: { state: 'dark', reason: 'census_gated' },
      }),
    ]);
    fixture.detectChanges();
    expect(component.liveCount()).toBe(1);
    expect(component.darkCount()).toBe(1);
  });

  it('shows a LIVE family as live', () => {
    fixture.componentRef.setInput('families', [family({ id: 'F1' })]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="family-live"]')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="family-dark"]')).toBeNull();
  });

  it('shows a DARK family with its reason (never a fabricated zero)', () => {
    fixture.componentRef.setInput('families', [
      family({
        id: 'F6',
        live_series_count: 0,
        availability: { state: 'dark', reason: 'census_gated' },
      }),
    ]);
    fixture.detectChanges();
    const dark = fixture.nativeElement.querySelector('[data-test="family-dark"]');
    expect(dark).not.toBeNull();
    const reason = fixture.nativeElement.querySelector('[data-reason="census_gated"]');
    expect(reason).not.toBeNull();
    expect((reason.textContent ?? '').trim()).toBe('the census sampler is gated');
  });

  it('renders an UNKNOWN reason as unknown(<value>)', () => {
    fixture.componentRef.setInput('families', [
      family({
        id: 'F99',
        availability: { state: 'dark', reason: 'a_reason_the_door_has_not_defined_yet' },
      }),
    ]);
    fixture.detectChanges();
    const reason = fixture.nativeElement.querySelector(
      '[data-reason="a_reason_the_door_has_not_defined_yet"]',
    );
    expect(reason).not.toBeNull();
    expect((reason.textContent ?? '').trim()).toBe(
      'unknown(a_reason_the_door_has_not_defined_yet)',
    );
  });

  it('renders the DECLARED label value sets as a set, never as a concrete pair (W-6)', () => {
    fixture.componentRef.setInput('families', [
      family({
        id: 'F7',
        names: [
          {
            name: 'ensemble_checkpoint_op_bytes',
            kind: 'histogram',
            labels: { op: ['aget_tuple', 'aget'] },
            help: '',
            buckets: [1024, 65536],
            cardinality_class: 'closed',
            monotonic: false,
            chart_role: 'line',
          },
        ],
      }),
    ]);
    fixture.detectChanges();
    const declared = fixture.nativeElement.querySelector(
      '[data-test="declared-labels-ensemble_checkpoint_op_bytes"]',
    );
    expect(declared).not.toBeNull();
    const text = (declared.textContent ?? '').trim();
    // A DECLARED vocabulary renders as a SET (op∈{…}) — the W-6
    // symptom (`op=aget,aget_tuple`, one concrete value) never appears.
    expect(text).toBe('op∈{aget, aget_tuple}');
    expect(text).not.toContain('op=aget');
  });

  it('renders NO declared-labels line for a name without a declared set', () => {
    fixture.componentRef.setInput('families', [
      family({
        id: 'F4',
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
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('[data-test="declared-labels-ensemble_db_pool_requests_errors_total"]'),
    ).toBeNull();
  });

  it('invokes the reload callback the shell handed over', () => {
    const reload = jest.fn();
    fixture.componentRef.setInput('onReload', reload);
    fixture.detectChanges();
    const button = fixture.nativeElement.querySelector('[data-test="families-reload"]');
    expect(button).not.toBeNull();
    button.click();
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
