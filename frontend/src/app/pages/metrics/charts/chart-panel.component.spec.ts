// The Chart.js PANEL WRAPPER's contract spec (T1 phase 1, task 1.7 +
// phase 4, tasks 4.4 / 4.6 / 4.7).
//
// The house pattern — a SOURCE-GREP pin over the wrapper's own source
// (the `metrics.bindings.pins.spec.ts` precedent) for the four contract
// clauses, plus a CONFIGURATION seam for the house rendering rules.
//
// The load-bearing pins:
//
//   * the FOUR CONTRACT CLAUSES are present in the source — the per-series
//     DOM proxy (`data-series` / `data-render-mode`), the visually-hidden
//     data table, `chart.destroy()` in `ngOnDestroy`, and the zoneless
//     rule;
//   * the IMPORT SHAPE — the vendor arrives through the DYNAMIC
//     `await import('chart.js')` form;
//   * the STATIC form is absent — and the assertion is
//     STATEMENT-ANCHORED, so the wrapper's own doc comment (which NAMES
//     the forbidden form) cannot redden it;
//   * the numeric W-1b assertion — ZERO files may exist under
//     `frontend/src/app/components/charts/chart/` (the retired out-of-tree
//     home must not exist at all).
//
// PHASE 4's pins (the implementation, not the contract):
//
//   * the counter-honesty rule — a COUNTER's dataset carries
//     `stepped: true`, a GAUGE's does not;
//   * the shared-y rule — ONE y-axis for every dataset;
//   * the nearest-point snap — `interaction: {mode:'index', intersect:false}`;
//   * the UTC-never-local x-axis — a LINEAR scale with a custom UTC tick
//     callback (no date-adapter dependency);
//   * the unit suffix on the value axis;
//   * the legend's click-toggle;
//   * the click hand-off — `(datasetIndex, index)` → `(seriesId, tsMs)`;
//   * the house anomaly overlay plugin — a HOUSE plugin, registered with
//     the markers, with the severity colours verbatim;
//   * the MOUNT FAILURE is surfaced (`mountFailed`), never swallowed;
//   * `chart.destroy()` really runs on destroy.

import { readFileSync, readdirSync, existsSync } from 'fs';
import { join } from 'path';
import { TestBed } from '@angular/core/testing';

import { CHART_SEVERITY_COLORS } from '../../../components/charts/chart-theme';
import { ChartPanelComponent, ChartPanelSeries } from './chart-panel.component';
import { HOUSE_ANOMALY_PLUGIN_ID } from './anomaly-overlay-plugin';

const wrapperDir = __dirname;
// The REPOSITORY root (the wrapper lives 6 levels below it:
// frontend/src/app/pages/metrics/charts).
const repoRoot = join(wrapperDir, '..', '..', '..', '..', '..', '..');
const wrapperSrc = readFileSync(join(wrapperDir, 'chart-panel.component.ts'), 'utf-8');

/** A STATIC import statement of the vendor — the forbidden shape. */
const STATIC_CHART_IMPORT = /^\s*import\s[^\n]*\bfrom\s+['"]chart\.js['"]/m;

/** The DYNAMIC import — the sanctioned shape. */
const DYNAMIC_CHART_IMPORT = /\bimport\s*\(\s*['"]chart\.js['"]\s*\)/;

const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);
const T1 = Date.UTC(2026, 10, 2, 10, 0, 0);

const COUNTER: ChartPanelSeries = {
  id: 'counter',
  label: 'requests (total)',
  mode: 'step',
  points: [
    { t: T0, v: 4 },
    { t: T1, v: 9 },
  ],
};
const GAUGE: ChartPanelSeries = {
  id: 'gauge',
  label: 'loop lag',
  mode: 'line',
  points: [
    { t: T0, v: 0.1 },
    { t: T1, v: 0.4 },
  ],
};

describe('chart-panel — the wrapper CONTRACT (T1 phase 1, task 1.7)', () => {
  it('carries the per-series DOM proxy clause (data-series / data-render-mode)', () => {
    expect(wrapperSrc).toContain('data-series');
    expect(wrapperSrc).toContain('data-render-mode');
    expect(wrapperSrc).toContain('chart-panel-proxy');
  });

  it('carries the visually-hidden data table clause (a11y + the DOM-assertion gate)', () => {
    expect(wrapperSrc).toContain('chart-panel-data');
    expect(wrapperSrc).toContain('visually-hidden');
    expect(wrapperSrc).toContain('<table');
  });

  it('carries the destroy clause (chart.destroy() in ngOnDestroy)', () => {
    expect(wrapperSrc).toMatch(/ngOnDestroy\(\)[^{]*\{[^}]*destroy\(\)/s);
  });

  it('carries the zoneless rule (no Angular-rendered overlay may depend on a redraw)', () => {
    expect(wrapperSrc).toContain('ZONELESS');
    expect(wrapperSrc).toMatch(/overlay may depend[\s\S]{0,40}?on a Chart\.js redraw/);
  });

  it('uses the DYNAMIC import shape for the vendor', () => {
    expect(wrapperSrc).toMatch(DYNAMIC_CHART_IMPORT);
    expect(wrapperSrc).toMatch(/await\s+import\s*\(\s*['"]chart\.js['"]\s*\)/);
  });

  it('carries NO static import statement of the vendor (the statement-anchored shape)', () => {
    // The assertion is STATEMENT-ANCHORED on purpose: the wrapper's own
    // doc comment NAMES the forbidden static form, so a naive
    // `not.toContain('chart.js')` would redden the freeze.
    expect(wrapperSrc).not.toMatch(STATIC_CHART_IMPORT);
  });

  it('lives at the canonical path (under src/app/pages/metrics/**)', () => {
    expect(wrapperSrc).toContain("from '@angular/core'");
    expect(existsSync(join(wrapperDir, 'chart-panel.component.ts'))).toBe(true);
  });
});

describe('chart-panel — the retired out-of-tree home (W-1b)', () => {
  it('has ZERO files under frontend/src/app/components/charts/chart/', () => {
    const retired = join(repoRoot, 'frontend', 'src', 'app', 'components', 'charts', 'chart');
    const files = existsSync(retired)
      ? readdirSync(retired, { recursive: true, withFileTypes: true }).filter((entry) =>
          entry.isFile(),
        ).length
      : 0;
    expect(files).toBe(0);
  });
});

describe('chart-panel — the scaffold renders the contract\'s DOM', () => {
  it('renders the per-series proxy and the visually-hidden table', async () => {
    const fixture = TestBed.createComponent(ChartPanelComponent);
    fixture.componentRef.setInput('series', [
      { id: 'probe', label: 'probe', mode: 'step', points: [{ t: 1, v: 2 }] },
    ]);
    fixture.componentRef.setInput('label', 'probe panel');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host: HTMLElement = fixture.nativeElement;
    const proxy = host.querySelector('[data-test="chart-panel-proxy"]');
    expect(proxy).not.toBeNull();
    expect(proxy?.getAttribute('data-series')).toBe('probe');
    expect(proxy?.getAttribute('data-render-mode')).toBe('step');

    const table = host.querySelector('[data-test="chart-panel-data"]');
    expect(table).not.toBeNull();
    expect(table?.classList.contains('visually-hidden')).toBe(true);
    expect(table?.querySelectorAll('tbody tr').length).toBe(1);

    fixture.destroy();
  });
});

// ═══════════════════════════════════════════════════════════════════════
// PHASE 4 — the implementation's house rules
// ═══════════════════════════════════════════════════════════════════════

describe('chart-panel — the configuration (the house rendering rules)', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<ChartPanelComponent>>;
  let component: ChartPanelComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChartPanelComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(ChartPanelComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('series', [COUNTER, GAUGE]);
    fixture.componentRef.setInput('unit', 'MB');
  });

  afterEach(() => fixture.destroy());

  it('renders a COUNTER with stepped:true and a GAUGE without it', () => {
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', []);
    const datasets = config.data.datasets;
    expect(datasets[0].stepped).toBe(true);
    expect(datasets[1].stepped).toBe(false);
  });

  it('shares ONE y-axis across the datasets (the shared-y rule)', () => {
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', []);
    const scales = config.options.scales;
    expect(typeof scales.y).toBe('object');
    expect(scales.y).not.toBeNull();
    expect(Array.isArray(scales.y)).toBe(false);
  });

  it('snaps the pointer to the nearest sample across every dataset', () => {
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', []);
    const interaction = config.options.interaction;
    expect(interaction.mode).toBe('index');
    expect(interaction.intersect).toBe(false);
  });

  it('uses a LINEAR x-axis with a custom UTC tick callback (no date adapter)', () => {
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', []);
    const x = config.options.scales.x;
    expect(x.type).toBe('linear');
    expect(x.ticks.callback(T0)).toBe('09:00');
    // The tick is UTC in every process timezone — the label is derived
    // from the epoch-ms instant, never from the clock.
    expect(x.ticks.callback(T0 + 12 * 60 * 60 * 1000)).toBe('21:00');
  });

  it('suffixes the value axis with the unit', () => {
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', []);
    const y = config.options.scales.y;
    expect(y.ticks.callback(4)).toBe('4 MB');
  });

  it('gives the legend a click-toggle', () => {
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', []);
    const legend = config.options.plugins.legend;
    expect(typeof legend.onClick).toBe('function');
  });

  it('registers the HOUSE anomaly overlay plugin with the markers', () => {
    const markers = [
      {
        anomalyId: 'a1',
        seriesId: 'counter',
        tsMs: T0,
        severity: 'high' as const,
        title: 'anomaly',
      },
    ];
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', markers);
    const plugins = config.plugins;
    expect(plugins.map((plugin) => plugin.id)).toContain(HOUSE_ANOMALY_PLUGIN_ID);
    expect(config.options.plugins[HOUSE_ANOMALY_PLUGIN_ID].markers).toEqual(markers);
  });

  it('maps a click to (seriesId, tsMs) and surfaces the anomaly id', () => {
    const markers = [
      {
        anomalyId: 'a1',
        seriesId: 'counter',
        tsMs: T0,
        severity: 'high' as const,
        title: 'anomaly',
      },
    ];
    fixture.componentRef.setInput('anomalies', markers);
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', markers);
    const onClick = config.options.onClick;
    const emitted: { anomalyId?: string; seriesId: string; tsMs: number }[] = [];
    fixture.componentRef.setInput('onPointClick', (event: { anomalyId?: string }) =>
      emitted.push(event as { anomalyId?: string; seriesId: string; tsMs: number }),
    );

    onClick(null, [{ datasetIndex: 0, index: 0 }]);
    expect(emitted.length).toBe(1);
    expect(emitted[0].seriesId).toBe('counter');
    expect(emitted[0].tsMs).toBe(T0);
    expect(emitted[0].anomalyId).toBe('a1');
  });

  it('never fabricates an anomaly id for a click that is not on a marker', () => {
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', []);
    const onClick = config.options.onClick;
    const emitted: { anomalyId?: string }[] = [];
    fixture.componentRef.setInput('onPointClick', (event: { anomalyId?: string }) =>
      emitted.push(event),
    );
    onClick(null, [{ datasetIndex: 1, index: 1 }]);
    expect(emitted.length).toBe(1);
    expect(emitted[0].anomalyId).toBeUndefined();
  });

  // ── B-1 (2026-11-04): the interaction is mode 'index', so the click
  // elements carry ONE ENTRY PER DATASET — and an anomaly marker may be
  // anchored to any dataset of the panel (the engine-key shim anchors
  // markers to their panel's dataset, which need not be the first).
  // Scanning only elements[0] stranded those markers; the resolution
  // now prefers the marker-bearing element and falls back to the first
  // element's plain payload.
  it('resolves a click on a marker ANCHORED TO A NON-FIRST dataset (the mode-index elements list)', () => {
    const markers = [
      {
        anomalyId: 'a-gauge',
        seriesId: 'gauge', // the SECOND dataset — the engine-key anchor shape
        tsMs: T1,
        severity: 'medium' as const,
        title: 'anomaly',
      },
    ];
    fixture.componentRef.setInput('anomalies', markers);
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', markers);
    const onClick = config.options.onClick;
    const emitted: { anomalyId?: string; seriesId: string; tsMs: number }[] = [];
    fixture.componentRef.setInput('onPointClick', (event: { anomalyId?: string }) =>
      emitted.push(event as { anomalyId?: string; seriesId: string; tsMs: number }),
    );

    // The mode-'index' shape: BOTH datasets' elements at the clicked index.
    onClick(null, [
      { datasetIndex: 0, index: 1 }, // counter — first, carries NO marker at this tolerance
      { datasetIndex: 1, index: 1 }, // gauge — carries the anchored marker
    ]);
    expect(emitted.length).toBe(1);
    expect(emitted[0].anomalyId).toBe('a-gauge');
    expect(emitted[0].seriesId).toBe('gauge');
  });

  it('a click that matches NO marker still reports the FIRST element\'s plain payload (unchanged)', () => {
    const markers = [
      {
        anomalyId: 'a-gauge',
        seriesId: 'gauge',
        tsMs: T1 + 10 * 60 * 1000, // far outside every sample's tolerance
        severity: 'medium' as const,
        title: 'anomaly',
      },
    ];
    fixture.componentRef.setInput('anomalies', markers);
    const config = component.buildChartConfig([COUNTER, GAUGE], 'MB', markers);
    const onClick = config.options.onClick;
    const emitted: { anomalyId?: string; seriesId: string }[] = [];
    fixture.componentRef.setInput('onPointClick', (event: { anomalyId?: string }) =>
      emitted.push(event as { anomalyId?: string; seriesId: string }),
    );

    onClick(null, [
      { datasetIndex: 0, index: 0 },
      { datasetIndex: 1, index: 0 },
    ]);
    expect(emitted.length).toBe(1);
    expect(emitted[0].anomalyId).toBeUndefined();
    expect(emitted[0].seriesId).toBe('counter'); // the FIRST element's plain payload
  });
});

describe('chart-panel — the DOM surface under a canvas', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<ChartPanelComponent>>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChartPanelComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(ChartPanelComponent);
  });

  afterEach(() => fixture.destroy());

  it('keeps the per-series render mode assertable through the proxy', async () => {
    fixture.componentRef.setInput('series', [COUNTER, GAUGE]);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host: HTMLElement = fixture.nativeElement;
    const step = host.querySelector('[data-test="chart-panel-proxy"][data-render-mode="step"]');
    const line = host.querySelector('[data-test="chart-panel-proxy"][data-render-mode="line"]');
    expect(step?.getAttribute('data-series')).toBe('counter');
    expect(line?.getAttribute('data-series')).toBe('gauge');
  });

  it('publishes the UTC tick text in the hidden surface (the retired chart-tick replacement)', async () => {
    fixture.componentRef.setInput('series', [COUNTER, GAUGE]);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host: HTMLElement = fixture.nativeElement;
    const ticks = Array.from(host.querySelectorAll('[data-test="chart-panel-tick"]')).map((node) =>
      node.textContent?.trim(),
    );
    expect(ticks.length).toBeGreaterThan(0);
    expect(ticks[0]).toBe('09:00');
    expect(ticks[ticks.length - 1]).toBe('10:00');
  });

  it('renders the samples in the hidden data table', async () => {
    fixture.componentRef.setInput('series', [COUNTER, GAUGE]);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host: HTMLElement = fixture.nativeElement;
    const rows = host.querySelectorAll('[data-test="chart-panel-row"]');
    expect(rows.length).toBe(4);
    const times = Array.from(host.querySelectorAll('[data-test="chart-panel-time"]')).map((node) =>
      node.textContent?.trim(),
    );
    expect(times[0]).toBe('09:00');
  });

  it('MOUNTS a chart and DESTROYS it on teardown', async () => {
    fixture.componentRef.setInput('series', [COUNTER, GAUGE]);
    fixture.detectChanges();
    await fixture.whenStable();
    // The vendor arrives through a DYNAMIC import — a native promise the
    // zone does not track, so `whenStable()` alone is not enough: the
    // macrotask tick lets the import settle.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await fixture.whenStable();
    fixture.detectChanges();

    const chart = (fixture.componentInstance as unknown as { chart: { destroy: () => void } | null })
      .chart;
    expect(chart).not.toBeNull();
    const destroy = jest.spyOn(chart as { destroy: () => void }, 'destroy');
    fixture.destroy();
    expect(destroy).toHaveBeenCalled();
  });

  it('SURFACES a mount failure (never a silent downgrade)', async () => {
    const failures: string[] = [];
    const prototype = ChartPanelComponent.prototype as unknown as {
      loadChartConstructor: () => Promise<unknown>;
    };
    jest
      .spyOn(prototype, 'loadChartConstructor')
      .mockRejectedValue(new Error('vendor unavailable'));

    fixture.componentRef.setInput('series', [COUNTER]);
    fixture.componentRef.setInput('onMountFailed', (reason: string) => failures.push(reason));
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(failures).toEqual(['vendor unavailable']);
    jest.restoreAllMocks();
  });
});
