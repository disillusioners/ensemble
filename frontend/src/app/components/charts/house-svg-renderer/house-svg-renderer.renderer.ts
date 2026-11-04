import {
  ChartCapabilities,
  ChartIntent,
  ChartOutputHandlers,
  ChartRenderData,
  ChartRenderer,
  ChartRendererPlan,
  ChartRendererRegistry,
  ChartRequest,
} from '../renderer-registry';
import { HouseSvgRendererComponent } from './house-svg-renderer.component';

/**
 * The HOUSE SVG renderer's REGISTRATION (T1 phase 4, task 4.8).
 *
 * The house engine serves TWO roles (IMPL-04), so it is registered
 * twice — once per role — and the two registrations differ only in the
 * `variant` they bind:
 *
 *   * the **mini** role (`sparkline`) — the sparkline-scale shape, no
 *     axis. Per R-2 no T1 surface renders it: the capability is
 *     retained, not shipped;
 *   * the **fallback** role (`trend`) — the shape a failed Chart.js
 *     mount drops to, and the static renderer the registry needs for
 *     its second fallback layer.
 *
 * The registration lives BESIDE the engine (not in the registry module)
 * so the registry stays mechanism-only and the tree's dependency
 * direction stays `pages → components`.
 */

/** The mini role's renderer id. */
export const HOUSE_SVG_MINI_RENDERER_ID = 'house-svg-mini';

/** The fallback role's renderer id. */
export const HOUSE_SVG_TREND_RENDERER_ID = 'house-svg-trend';

function capabilities(
  intents: readonly ChartIntent[],
  axis: ChartCapabilities['axis'],
): ChartCapabilities {
  return {
    intents,
    flags: ['markers', 'bands', 'drill'],
    surface: 'static',
    axis,
    interactive: false,
  };
}

function plan(variant: 'mini' | 'trend', rendererId: string): (data: ChartRenderData) => ChartRendererPlan {
  return (data: ChartRenderData) => ({
    rendererId,
    component: HouseSvgRendererComponent,
    inputs: (values: ChartRenderData, handlers?: ChartOutputHandlers) => ({
      series: values.series,
      anomalies: values.anomalies ?? [],
      baseline: values.baseline ?? [],
      variant,
      // The panel's unit — the trend role's value-axis suffix (phase 6,
      // task 6.2). 'unit unknown' renders verbatim; the mini ignores it
      // (it renders no axis).
      unit: values.unit ?? 'unit unknown',
      ariaLabel: values.label ?? 'Metric trend (UTC)',
      onAnomalyClick: handlers?.anomalyClick ?? null,
    }),
    capabilities: capabilities(
      variant === 'mini' ? ['sparkline'] : ['trend'],
      variant === 'mini' ? 'none' : 'time',
    ),
  });
}

/** The MINI role's registration (the sparkline shape — no axis). */
export function createHouseSvgMiniRenderer(): ChartRenderer {
  const id = HOUSE_SVG_MINI_RENDERER_ID;
  return {
    id,
    capabilities: capabilities(['sparkline'], 'none'),
    supports(request: ChartRequest): boolean {
      return (
        request.intent === 'sparkline' &&
        request.surface !== 'interactive' &&
        request.flags.every((flag) => ['markers', 'bands', 'drill'].includes(flag))
      );
    },
    plan: plan('mini', id),
  };
}

/** The FALLBACK role's registration (the static trend shape). */
export function createHouseSvgTrendRenderer(): ChartRenderer {
  const id = HOUSE_SVG_TREND_RENDERER_ID;
  return {
    id,
    capabilities: capabilities(['trend'], 'time'),
    supports(request: ChartRequest): boolean {
      return (
        request.intent === 'trend' &&
        request.surface !== 'interactive' &&
        request.flags.every((flag) => ['markers', 'bands', 'drill'].includes(flag))
      );
    },
    plan: plan('trend', id),
  };
}

/**
 * The registry of the STATIC renderers only.
 *
 * The resource timeline (the `custom` slot's module) needs the house
 * SVG and nothing else — it never imports the Chart.js panel, so the
 * interactive vendor never reaches the resources route.
 */
export function createHouseSvgRegistry(): ChartRendererRegistry {
  const registry = new ChartRendererRegistry();
  registry.register(createHouseSvgMiniRenderer());
  registry.register(createHouseSvgTrendRenderer());
  return registry;
}
