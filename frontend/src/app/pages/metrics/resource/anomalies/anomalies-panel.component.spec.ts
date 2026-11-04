// The ANOMALIES PANEL spec (T1 phase 5, tasks 5.5/5.6 — ADJ-v).
//
// The load-bearing pins:
//
//   * the table renders the API's facts — id / kind / severity / peak /
//     t_start / t_end (the facts that previously existed only as
//     anonymous timeline dots);
//   * a row click emits the WIRE anomaly to the body, which opens the
//     existing drill-down (the panel itself never fetches);
//   * the standing process-local disclosure is present, in ONE header
//     line, and its copy makes NO durability claim — it says "resets"
//     and "never spans a restart" (the copy review, asserted).

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import { Anomaly } from '../../../../models/resources.models';
import {
  ANOMALIES_PROCESS_LOCAL_DISCLOSURE,
  AnomaliesPanelComponent,
} from './anomalies-panel.component';

function makeAnomaly(overrides: Partial<Anomaly> = {}): Anomaly {
  return {
    id: 'anom-000003',
    kind: 'cpu',
    t_start: '2026-11-03T09:30:00',
    t_end: '2026-11-03T09:40:00',
    peak: 75.55,
    baseline: 12,
    severity: 'medium',
    series_key: 'daemon_cpu',
    shape: 'spike',
    ...overrides,
  };
}

describe('AnomaliesPanelComponent — the anomalies table (5.5)', () => {
  let fixture: ComponentFixture<AnomaliesPanelComponent>;
  let component: AnomaliesPanelComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AnomaliesPanelComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(AnomaliesPanelComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('anomalies', [
      makeAnomaly(),
      makeAnomaly({ id: 'anom-000004', kind: 'fds', severity: 'high', peak: 900 }),
    ]);
    fixture.detectChanges();
  });

  afterEach(() => fixture.destroy());

  it('renders one row per anomaly with the API\'s facts', () => {
    const rows = fixture.debugElement.queryAll(By.css('[data-test^="anomaly-row-"]'));
    expect(rows.length).toBe(2);

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('anom-000003');
    expect(text).toContain('cpu');
    expect(text).toContain('medium');
    expect(text).toContain('75.55');
    // The naive-UTC wire timestamps render as UTC clock labels (D10).
    expect(text).toContain('09:30');
    expect(text).toContain('09:40');

    const high = fixture.debugElement.query(By.css('[data-test="anomaly-row-anom-000004"]'));
    expect(high.nativeElement.getAttribute('data-severity')).toBe('high');
  });

  it('a row click emits the WIRE anomaly (the body opens the existing drill-down)', () => {
    const seen: Anomaly[] = [];
    component.rowClick.subscribe((anomaly) => seen.push(anomaly));

    const row = fixture.debugElement.query(By.css('[data-test="anomaly-row-anom-000003"]'));
    row.triggerEventHandler('click', null);
    fixture.detectChanges();

    expect(seen.length).toBe(1);
    expect(seen[0].id).toBe('anom-000003');
    expect(seen[0].series_key).toBe('daemon_cpu');

    // The keyboard path emits the same anomaly.
    row.triggerEventHandler('keydown.enter', null);
    expect(seen.length).toBe(2);
  });

  it('renders the empty state when the window has no anomalies', () => {
    fixture.componentRef.setInput('anomalies', []);
    fixture.componentRef.setInput('loading', false);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="anomalies-empty"]')).not.toBeNull();
  });

  it('a failed load shows the error branch and Retry hands back to the body', () => {
    const retries: number[] = [];
    component.retryRequested.subscribe(() => retries.push(1));
    fixture.componentRef.setInput('anomalies', []);
    fixture.componentRef.setInput('loading', false);
    fixture.componentRef.setInput('error', 'Unable to load anomalies — showing the last known list.');
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-test="anomalies-error"]')).not.toBeNull();
    fixture.debugElement
      .query(By.css('[data-test="anomalies-retry"]'))
      .nativeElement.click();
    expect(retries).toEqual([1]);
  });
});

describe('AnomaliesPanelComponent — the standing process-local disclosure (5.6)', () => {
  let fixture: ComponentFixture<AnomaliesPanelComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AnomaliesPanelComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(AnomaliesPanelComponent);
    fixture.componentRef.setInput('anomalies', [makeAnomaly()]);
    fixture.detectChanges();
  });

  afterEach(() => fixture.destroy());

  it('the disclosure element exists, in the panel header', () => {
    const header = fixture.debugElement.query(By.css('.anomalies-header'));
    expect(header).not.toBeNull();
    const disclosure = fixture.debugElement.query(By.css('[data-test="anomalies-disclosure"]'));
    expect(disclosure).not.toBeNull();
    // ONE header line — the disclosure lives INSIDE the header block.
    expect(disclosure.nativeElement.parentElement).toBe(header.nativeElement);
  });

  it('the copy states the reset + the 256-entry bound — and NO durability claim', () => {
    const copy = (fixture.nativeElement.querySelector('[data-test="anomalies-disclosure"]')?.textContent ?? '') as string;
    // The facts: process-local, resets on restart, bounded at 256.
    expect(copy).toContain('Process-local');
    expect(copy).toContain('resets on daemon restart');
    expect(copy).toContain('256');
    // The copy review (ADJ-v): it can never be read as the durable
    // event history — the copy names the limit explicitly.
    expect(copy).toContain('never spans a restart');
    // NO durability wording anywhere in the disclosure.
    expect(/durable|persist|retained across|survives/i.test(copy)).toBe(false);
  });

  it('the disclosure constant is the copy\'s SSOT', () => {
    expect(ANOMALIES_PROCESS_LOCAL_DISCLOSURE).toContain('resets on daemon restart');
    expect(ANOMALIES_PROCESS_LOCAL_DISCLOSURE).toContain('256');
    expect(ANOMALIES_PROCESS_LOCAL_DISCLOSURE).toContain('never spans a restart');
  });
});
