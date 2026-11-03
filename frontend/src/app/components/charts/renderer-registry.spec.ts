// The INTENT → RENDERER REGISTRY's spec (T1 phase 4, task 4.8).
//
// The load-bearing pins — the THREE-LAYER FALLBACK:
//
//   1. an intent with no supporting renderer fails LOUDLY at
//      build/test time (never a silent "something that draws");
//   2. a runtime mount failure SURFACES a badge and falls back to the
//      static renderer;
//   3. a planted "no fallback" case is a HARD ERROR.
//
// A SILENT downgrade is the failure mode the house forbids, and the
// spec proves it impossible: every fallback carries a badge, and a
// missing fallback throws.

import {
  ChartCapabilities,
  ChartRegistryError,
  ChartRenderData,
  ChartRenderer,
  ChartRendererPlan,
  ChartRendererRegistry,
  ChartRequest,
  UnsupportedChartIntentError,
  assertChartRegistryIsWellFormed,
  chartMountFallback,
} from './renderer-registry';
import {
  HOUSE_SVG_MINI_RENDERER_ID,
  HOUSE_SVG_TREND_RENDERER_ID,
  createHouseSvgMiniRenderer,
  createHouseSvgRegistry,
  createHouseSvgTrendRenderer,
} from './house-svg-renderer/house-svg-renderer.renderer';

function capabilities(overrides: Partial<ChartCapabilities> = {}): ChartCapabilities {
  return {
    intents: ['trend'],
    flags: ['markers', 'drill'],
    surface: 'static',
    axis: 'time',
    interactive: false,
    ...overrides,
  };
}

function fakeRenderer(
  id: string,
  caps: ChartCapabilities,
  supports: (request: ChartRequest) => boolean = () => true,
): ChartRenderer {
  return {
    id,
    capabilities: caps,
    supports,
    plan: (): ChartRendererPlan => ({
      rendererId: id,
      component: class Fake {},
      inputs: () => ({}),
      capabilities: caps,
    }),
  };
}

const DATA: ChartRenderData = { series: [], label: 'probe' };

describe('renderer-registry — the build/test-time validation (layer 1)', () => {
  it('refuses an EMPTY registry', () => {
    expect(() => assertChartRegistryIsWellFormed(new ChartRendererRegistry())).toThrow(
      ChartRegistryError,
    );
  });

  it('refuses a registry that does not serve every intent', () => {
    const registry = new ChartRendererRegistry();
    registry.register(
      fakeRenderer('only-sparkline', capabilities({ intents: ['sparkline'] }), (request) =>
        request.intent === 'sparkline',
      ),
    );
    expect(() => assertChartRegistryIsWellFormed(registry)).toThrow(
      /no renderer serves the intent 'trend'/,
    );
  });

  it('refuses a registry with NO static renderer (nothing could fall back)', () => {
    const registry = new ChartRendererRegistry();
    registry.register(
      fakeRenderer(
        'only-interactive',
        capabilities({ surface: 'interactive', intents: ['trend', 'stat'] }),
        (request) => request.intent !== 'sparkline',
      ),
    );
    expect(() => assertChartRegistryIsWellFormed(registry)).toThrow(
      /no static renderer is registered/,
    );
  });

  it('refuses a renderer whose supports() contradicts its declared capabilities', () => {
    const registry = new ChartRendererRegistry();
    registry.register(
      fakeRenderer(
        'liar',
        capabilities({ intents: ['trend'], flags: ['markers', 'drill'] }),
        () => false,
      ),
    );
    expect(() => assertChartRegistryIsWellFormed(registry)).toThrow(
      /declares the intent 'trend'/,
    );
  });

  it('resolve() THROWS for an intent nobody serves (the loud failure)', () => {
    const registry = new ChartRendererRegistry();
    registry.register(
      fakeRenderer('only-sparkline', capabilities({ intents: ['sparkline'] }), (request) =>
        request.intent === 'sparkline',
      ),
    );
    expect(() => registry.resolve({ intent: 'trend', flags: [] })).toThrow(
      UnsupportedChartIntentError,
    );
    expect(() => registry.plan({ intent: 'trend', flags: [] }, DATA)).toThrow(
      UnsupportedChartIntentError,
    );
  });
});

describe('renderer-registry — the runtime mount-fallback (layer 2)', () => {
  it('falls back to the static renderer and SURFACES a badge', () => {
    const registry = new ChartRendererRegistry();
    registry.register(
      fakeRenderer(
        'interactive',
        capabilities({ surface: 'interactive', intents: ['trend', 'stat'] }),
        (request) => request.intent !== 'sparkline',
      ),
    );
    registry.register(
      fakeRenderer('static', capabilities({ intents: ['trend'] }), (request) =>
        request.intent === 'trend',
      ),
    );

    const outcome = chartMountFallback(
      registry,
      { intent: 'trend', flags: [] },
      DATA,
      'vendor unavailable',
    );
    expect(outcome.fellBack).toBe(true);
    expect(outcome.plan.rendererId).toBe('static');
    // The badge is the SURFACED downgrade — never empty.
    expect(outcome.badge.length).toBeGreaterThan(0);
    expect(outcome.badge).toContain('vendor unavailable');
  });

  it('never returns a fallback WITHOUT a badge (the silent downgrade is impossible)', () => {
    const registry = new ChartRendererRegistry();
    registry.register(
      fakeRenderer('static', capabilities({ intents: ['trend'] }), (request) =>
        request.intent === 'trend',
      ),
    );
    const outcome = chartMountFallback(
      registry,
      { intent: 'trend', flags: [] },
      DATA,
      'boom',
    );
    expect(outcome.badge.trim().length).toBeGreaterThan(0);
  });
});

describe('renderer-registry — the hard error (layer 3)', () => {
  it('THROWS when there is NO fallback renderer', () => {
    const registry = new ChartRendererRegistry();
    registry.register(
      fakeRenderer(
        'interactive-only',
        capabilities({ surface: 'interactive', intents: ['trend', 'stat'] }),
        (request) => request.intent !== 'sparkline',
      ),
    );
    expect(() =>
      chartMountFallback(registry, { intent: 'trend', flags: [] }, DATA, 'boom'),
    ).toThrow(ChartRegistryError);
    expect(() => registry.fallbackPlan({ intent: 'trend', flags: [] }, DATA)).toThrow(
      /a mount failure would be a silent downgrade/,
    );
  });
});

describe('renderer-registry — the house SVG registrations', () => {
  it('registers the mini role (sparkline, no axis) and the fallback role (trend, time axis)', () => {
    const mini = createHouseSvgMiniRenderer();
    const trend = createHouseSvgTrendRenderer();

    expect(mini.id).toBe(HOUSE_SVG_MINI_RENDERER_ID);
    expect(mini.capabilities.axis).toBe('none');
    expect(mini.capabilities.surface).toBe('static');
    expect(mini.supports({ intent: 'sparkline', flags: [] })).toBe(true);
    expect(mini.supports({ intent: 'trend', flags: [] })).toBe(false);

    expect(trend.id).toBe(HOUSE_SVG_TREND_RENDERER_ID);
    expect(trend.capabilities.axis).toBe('time');
    expect(trend.supports({ intent: 'trend', flags: [] })).toBe(true);
    expect(trend.supports({ intent: 'trend', flags: [], surface: 'interactive' })).toBe(false);
  });

  it('the static registry is well formed and resolves both roles', () => {
    const registry = createHouseSvgRegistry();
    // The static registry owns the trend + sparkline intents; the `stat`
    // intent belongs to the interactive panel, which this registry does
    // not carry (the resources route never needs it).
    expect(() =>
      assertChartRegistryIsWellFormed(registry, ['trend', 'sparkline']),
    ).not.toThrow();
    // Asking for the FULL vocabulary of a subset registry is refused —
    // loudly, at build/test time.
    expect(() => assertChartRegistryIsWellFormed(registry)).toThrow(
      /no renderer serves the intent 'stat'/,
    );
    expect(registry.resolve({ intent: 'sparkline', flags: [] }).id).toBe(
      HOUSE_SVG_MINI_RENDERER_ID,
    );
    expect(registry.resolve({ intent: 'trend', flags: [] }).id).toBe(
      HOUSE_SVG_TREND_RENDERER_ID,
    );
  });

  it('the static registry has NO interactive renderer (the vendor never reaches it)', () => {
    const registry = createHouseSvgRegistry();
    expect(registry.registered.every((renderer) => renderer.capabilities.surface === 'static')).toBe(
      true,
    );
  });
});
