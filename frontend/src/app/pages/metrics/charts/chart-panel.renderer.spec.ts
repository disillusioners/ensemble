// The DEFAULT chart registry's spec (T1 phase 4, task 4.8).
//
// The default registry is the house SVG (the mini role + the fallback
// role) PLUS the Chart.js panel (the interactive role). The load-bearing
// pins:
//
//   * the registry is WELL FORMED (every intent is served, a static
//     renderer exists, no renderer contradicts its own capabilities);
//   * a `trend` request lands on the INTERACTIVE panel;
//   * a request that names the `static` surface lands on the house SVG;
//   * the fallback of a failed panel mount is the house SVG.

import {
  ChartRenderData,
  assertChartRegistryIsWellFormed,
  chartMountFallback,
} from '../../../components/charts/renderer-registry';
import {
  HOUSE_SVG_MINI_RENDERER_ID,
  HOUSE_SVG_TREND_RENDERER_ID,
} from '../../../components/charts/house-svg-renderer/house-svg-renderer.renderer';
import {
  CHART_PANEL_RENDERER_ID,
  createDefaultChartRegistry,
} from './chart-panel.renderer';

const DATA: ChartRenderData = { series: [], label: 'probe' };

describe('the default chart registry', () => {
  it('is WELL FORMED (the build/test-time validation passes)', () => {
    expect(() => assertChartRegistryIsWellFormed(createDefaultChartRegistry())).not.toThrow();
  });

  it('resolves a trend request to the INTERACTIVE panel', () => {
    const registry = createDefaultChartRegistry();
    expect(registry.resolve({ intent: 'trend', flags: [] }).id).toBe(CHART_PANEL_RENDERER_ID);
    expect(registry.resolve({ intent: 'stat', flags: [] }).id).toBe(CHART_PANEL_RENDERER_ID);
  });

  it('resolves a sparkline request to the house SVG mini role', () => {
    const registry = createDefaultChartRegistry();
    expect(registry.resolve({ intent: 'sparkline', flags: [] }).id).toBe(
      HOUSE_SVG_MINI_RENDERER_ID,
    );
  });

  it('resolves a STATIC-surface trend request to the house SVG', () => {
    const registry = createDefaultChartRegistry();
    expect(registry.resolve({ intent: 'trend', flags: [], surface: 'static' }).id).toBe(
      HOUSE_SVG_TREND_RENDERER_ID,
    );
  });

  it('falls back to the house SVG when the panel fails to mount', () => {
    const registry = createDefaultChartRegistry();
    const outcome = chartMountFallback(
      registry,
      { intent: 'trend', flags: [] },
      DATA,
      'vendor unavailable',
    );
    expect(outcome.fellBack).toBe(true);
    expect(outcome.plan.rendererId).toBe(HOUSE_SVG_TREND_RENDERER_ID);
    expect(outcome.badge).toContain('vendor unavailable');
  });

  it('the panel plan carries the pointClick hand-off (5.9 — the drill-down rides the wrapper)', () => {
    const registry = createDefaultChartRegistry();
    const plan = registry.plan({ intent: 'trend', flags: [] }, DATA);
    let seen: { anomalyId?: string; seriesId: string; tsMs: number } | null = null;
    const inputs = plan.inputs(DATA, {
      pointClick: (payload) => {
        seen = payload;
      },
    });
    // The handler travels as the wrapper's onPointClick INPUT.
    expect(typeof inputs['onPointClick']).toBe('function');
    (inputs['onPointClick'] as (payload: { anomalyId?: string; seriesId: string; tsMs: number }) => void)({
      anomalyId: 'a1',
      seriesId: 's',
      tsMs: 1,
    });
    expect(seen).toEqual({ anomalyId: 'a1', seriesId: 's', tsMs: 1 });

    // No handler → the input binds null (the wrapper never fabricates one).
    const bare = plan.inputs(DATA);
    expect(bare['onPointClick']).toBeNull();
  });

  it('the panel plan binds the unit, the series and the anomalies', () => {
    const registry = createDefaultChartRegistry();
    const plan = registry.plan({ intent: 'trend', flags: [] }, DATA);
    const inputs = plan.inputs({
      ...DATA,
      unit: 'MB',
      anomalies: [{ anomalyId: 'a1', seriesId: 's', tsMs: 1, severity: 'high', title: 'x' }],
    });
    expect(inputs['unit']).toBe('MB');
    expect(inputs['series']).toEqual([]);
    expect((inputs['anomalies'] as unknown[]).length).toBe(1);
  });
});
