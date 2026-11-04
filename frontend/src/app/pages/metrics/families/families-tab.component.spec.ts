// The FAMILY CATALOG spec (P5 task 5.4/5.7/5.8 · D1 pack `metrics-fe-page`;
// T1 phase 2, task 2.7 re-points it at the frame-level drawer).
//
// The load-bearing pins:
//
//   * a DARK family shows WHY it is dark — the honest signal is the
//     reason, never a fabricated zero. An unknown reason renders as
//     `unknown(<value>)` (AM-7) — the FE never invents a meaning for a
//     reason it does not know and never hides it;
//   * the ERROR GATE — "No families are registered." must NEVER render
//     under a failed load (the payload is UNKNOWN then, not empty);
//   * the SORTABLE / FILTERABLE columns with `aria-sort` — the dark
//     families must be findable;
//   * the dead visibility-flag input is GONE (the shell never passes it).
//
// NOTE: this file is INSIDE the pinned residue check's scope (the whole
// `families/` directory), so the word it hunts must not appear here
// either — not even in this comment.
//
// The FILE keeps its historical name (`families-tab.component.ts`) — the
// component is no longer a tab, but renaming the file would move the
// registered pack's D1 spec path (`test/packs/metrics_fe_dual_side_test.sh`)
// and this phase is FE-only. The rename is a routed follow-up.

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { MetricsFamily } from '../../../models/metrics.model';
import { MetricsFamilyCatalogComponent } from './families-tab.component';

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

describe('MetricsFamilyCatalogComponent — the availability rendering', () => {
  let fixture: ComponentFixture<MetricsFamilyCatalogComponent>;
  let component: MetricsFamilyCatalogComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsFamilyCatalogComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsFamilyCatalogComponent);
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

describe('MetricsFamilyCatalogComponent — the drawer affordances (2.7)', () => {
  let fixture: ComponentFixture<MetricsFamilyCatalogComponent>;
  let component: MetricsFamilyCatalogComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsFamilyCatalogComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsFamilyCatalogComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('families', [
      family({ id: 'F9' }),
      family({ id: 'F1' }),
      family({
        id: 'F6',
        live_series_count: 0,
        availability: { state: 'dark', reason: 'census_gated' },
      }),
    ]);
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  const rowIds = (): string[] =>
    Array.from(
      fixture.nativeElement.querySelectorAll('[data-family]') as NodeListOf<HTMLElement>,
    ).map((row) => row.getAttribute('data-family') ?? '');

  it('sorts by the clicked column (the click handler is wired, not inert)', () => {
    expect(rowIds()).toEqual(['F1', 'F6', 'F9']);
    const header = fixture.nativeElement.querySelector(
      '[data-test="families-th-id"]',
    ) as HTMLElement;
    header.click();
    fixture.detectChanges();
    expect(rowIds()).toEqual(['F9', 'F6', 'F1']);
  });

  it('carries aria-sort on every column header, and flips on a second click', () => {
    const header = fixture.nativeElement.querySelector(
      '[data-test="families-th-id"]',
    ) as HTMLElement;
    expect(header.getAttribute('aria-sort')).toBe('ascending');
    expect(
      (
        fixture.nativeElement.querySelector(
          '[data-test="families-th-role"]',
        ) as HTMLElement
      ).getAttribute('aria-sort'),
    ).toBe('none');
    header.click();
    fixture.detectChanges();
    expect(header.getAttribute('aria-sort')).toBe('descending');
  });

  it('filters the rows by the family id', () => {
    const input = fixture.nativeElement.querySelector(
      '[data-test="families-filter"]',
    ) as HTMLInputElement;
    input.value = 'F6';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(rowIds()).toEqual(['F6']);
  });

  it('says "No families match the filter" — never "No families are registered"', () => {
    const input = fixture.nativeElement.querySelector(
      '[data-test="families-filter"]',
    ) as HTMLInputElement;
    input.value = 'no-such-family';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('[data-test="families-no-match"]'),
    ).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="families-empty"]')).toBeNull();
  });

  it('the ERROR GATE — a failed load renders the error state, never the empty copy', () => {
    // The planted violation: the door failed, so the payload is UNKNOWN.
    fixture.componentRef.setInput('error', 'The metrics door is unreachable.');
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="families-error"]')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="families-empty"]')).toBeNull();
    const body = fixture.nativeElement.textContent ?? '';
    expect(body).not.toContain('No families are registered.');
  });

  it('the empty copy renders only under a HEALTHY, genuinely empty load', () => {
    fixture.componentRef.setInput('families', []);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="families-empty"]')).not.toBeNull();
  });
});

// ── THE PHASE-8 DISPOSITION (task 8.9) ───────────────────────────────────
//
// 8.9 asked for the INERT CATALOG TABLE to be fixed: the `<th>` cells
// get `aria-sort` + click sorting, the dark families become findable
// (a filter), and the empty copy is ERROR-GATED.
//
// **DISPOSITION: DELIVERED IN PHASE 2 (task 2.7)** — the affordances
// exist and each one is pinned (the legs above). The four legs below
// re-assert them TOGETHER so this phase's evidence carries the claim
// in one place, and they fail if any affordance is removed.

describe('MetricsFamilyCatalogComponent — the drawer affordances (T1 phase 8, task 8.9)', () => {
  let fixture: ComponentFixture<MetricsFamilyCatalogComponent>;
  let component: MetricsFamilyCatalogComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MetricsFamilyCatalogComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();
    fixture = TestBed.createComponent(MetricsFamilyCatalogComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('families', [
      family({ id: 'F1' }),
      family({
        id: 'F2',
        live_series_count: 0,
        availability: { state: 'dark', reason: 'capture_off' },
      }),
    ]);
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('every column header carries `aria-sort` and a click handler', () => {
    const headers = fixture.nativeElement.querySelectorAll('th');
    expect(headers.length).toBe(5);
    for (const header of Array.from(headers) as HTMLElement[]) {
      expect(header.getAttribute('aria-sort')).toBeTruthy();
    }
    // The sort is WIRED, not inert: a click re-orders the rows.
    const before = component.visibleFamilies().map((entry) => entry.id);
    fixture.nativeElement.querySelector('[data-test="families-th-id"]')?.click();
    fixture.detectChanges();
    const after = component.visibleFamilies().map((entry) => entry.id);
    expect(after).not.toEqual(before);
  });

  it('the dark families are FINDABLE (a filter narrows the rows)', () => {
    const input = fixture.nativeElement.querySelector('[data-test="families-filter"]');
    expect(input).not.toBeNull();
    component.onFilter({ target: { value: 'F2' } } as unknown as Event);
    fixture.detectChanges();
    expect(component.visibleFamilies().map((entry) => entry.id)).toEqual(['F2']);
    expect(fixture.nativeElement.querySelector('[data-test="families-no-match"]')).toBeNull();
  });

  it('the empty copy is ERROR-GATED (a failed load never says "No families are registered.")', () => {
    fixture.componentRef.setInput('error', 'The metrics door is unreachable.');
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="families-error"]')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="families-empty"]')).toBeNull();
  });

  it('the empty copy renders only under a HEALTHY, genuinely empty load', () => {
    fixture.componentRef.setInput('families', []);
    fixture.componentRef.setInput('error', null);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="families-empty"]')).not.toBeNull();
  });
});
