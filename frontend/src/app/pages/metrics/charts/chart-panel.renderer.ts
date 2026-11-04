import {
  ChartCapabilities,
  ChartOutputHandlers,
  ChartRenderData,
  ChartRenderer,
  ChartRendererPlan,
  ChartRendererRegistry,
  ChartRequest,
} from '../../../components/charts/renderer-registry';
import {
  createHouseSvgMiniRenderer,
  createHouseSvgTrendRenderer,
} from '../../../components/charts/house-svg-renderer/house-svg-renderer.renderer';
import { ChartPanelComponent } from './chart-panel.component';

/**
 * The CHART.JS PANEL's REGISTRATION (T1 phase 4, task 4.8).
 *
 * The interactive-panel role: the panel carries the unit-labeled value
 * axis, the crosshair + nearest-point snap, the legend's click-toggle,
 * the shared y-axis and the counter `stepped` rendering.
 *
 * The registration lives BESIDE the panel so the registry module stays
 * mechanism-only (it imports no engine) and the tree's dependency
 * direction stays `pages → components`.
 */

/** The Chart.js panel renderer's id. */
export const CHART_PANEL_RENDERER_ID = 'chartjs-panel';

const CAPABILITIES: ChartCapabilities = {
  intents: ['trend', 'stat'],
  flags: ['shared-y', 'markers', 'bands', 'drill'],
  surface: 'interactive',
  axis: 'unit',
  interactive: true,
};

/** The Chart.js panel's registration. */
export function createChartJsPanelRenderer(): ChartRenderer {
  return {
    id: CHART_PANEL_RENDERER_ID,
    capabilities: CAPABILITIES,
    supports(request: ChartRequest): boolean {
      return (
        request.intent !== 'sparkline' &&
        request.surface !== 'static' &&
        request.flags.every((flag) => CAPABILITIES.flags.includes(flag))
      );
    },
    plan: (data: ChartRenderData): ChartRendererPlan => ({
      rendererId: CHART_PANEL_RENDERER_ID,
      component: ChartPanelComponent,
      inputs: (values: ChartRenderData, handlers?: ChartOutputHandlers) => ({
        series: values.series,
        anomalies: values.anomalies ?? [],
        baseline: values.baseline ?? [],
        unit: values.unit ?? 'unit unknown',
        label: values.label ?? 'metrics panel',
        // The DATA-END marker (8.10): where the panel's data stops.
        dataEnd: values.dataEnd ?? null,
        onMountFailed: handlers?.mountFailed ?? null,
        // The drill-down hand-off (5.9): the wrapper's pointClick — with
        // the anomalyId when the click landed on a marker — reaches the
        // consumer through this input.
        onPointClick: handlers?.pointClick ?? null,
      }),
      capabilities: CAPABILITIES,
    }),
  };
}

/**
 * The DEFAULT registry — the house SVG (mini + fallback) plus the
 * Chart.js panel.
 *
 * The order is the resolution order: the interactive panel is
 * registered FIRST, so a `trend` request lands on it; a request that
 * names the `static` surface lands on the house SVG.
 */
export function createDefaultChartRegistry(): ChartRendererRegistry {
  const registry = new ChartRendererRegistry();
  registry.register(createChartJsPanelRenderer());
  registry.register(createHouseSvgMiniRenderer());
  registry.register(createHouseSvgTrendRenderer());
  return registry;
}
