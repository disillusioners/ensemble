// The CUSTOM SLOT spec (T1 phase 3, tasks 3.1 / 3.6 / 3.12).
//
// The load-bearing pins:
//
//   * the panel carries its OWN labelled range control ("Timeline range")
//     — the frame's central-sourced control does NOT govern it (AM-19);
//   * the option set is the frame's union with the options the resources
//     ladder cannot serve DISABLED (the visible enforcement);
//   * the mapping onto the resources ladder REFUSES a central-only window
//     — no `/api/resources/*` request can ever carry a window outside the
//     frozen `ResourcesWindow` union (the door would 400 on it);
//   * the retention-tier copy names the tiers — and the tiers are NEVER
//     offered as range options.
//
// The panel is a PLACEHOLDER until phase 5 (the six `/api/resources/*`
// endpoints); the phase-3 surface is the labelled control + the mapping
// guard + the copy.

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';

import { ResourcesWindow } from '../../../models/resources.models';
import { MetricsRangeOption } from '../metrics-range';
import { ResourceTabComponent } from './resource-tab.component';

describe('ResourceTabComponent — the panel\'s own range control (3.1 / AM-19)', () => {
  let fixture: ComponentFixture<ResourceTabComponent>;
  let component: ResourceTabComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResourceTabComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();
    fixture = TestBed.createComponent(ResourceTabComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  /** The rendered options of the panel's control, with their disabled flag. */
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
      providers: [provideNoopAnimations()],
    }).compileComponents();
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
    // … and the panel renders NO baseline surface at all.
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
      providers: [provideNoopAnimations()],
    }).compileComponents();
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
