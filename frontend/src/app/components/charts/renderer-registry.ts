import { Type } from '@angular/core';

import {
  ChartSeries,
  HouseAnomalyMarker,
} from './house-svg-renderer/house-svg-renderer';

/**
 * The INTENT → RENDERER REGISTRY (T1 phase 4, task 4.8).
 *
 * ── THE CONTRACT ────────────────────────────────────────────────────────
 *
 * A consumer says WHAT it wants (an **intent** plus **flags**) and the
 * registry says WHICH engine draws it. Adding an engine later is one
 * renderer file plus one registry line — the manifest never changes
 * (IMPL-04).
 *
 * The intent vocabulary is `trend | sparkline | stat`; the flags are
 * `shared-y | markers | bands | drill`.
 *
 * ── THE THREE-LAYER FALLBACK (never a silent downgrade) ────────────────
 *
 *   1. **BUILD/TEST-TIME VALIDATION, FIRST.** `assertChartRegistryIsWellFormed`
 *      refuses a registry whose shape is broken (an intent nobody
 *      serves, a renderer whose `supports()` contradicts its own
 *      declared capabilities, no fallback renderer at all), and
 *      `resolve()` throws `UnsupportedChartIntentError` for an intent
 *      nobody serves. The failure is LOUD and it happens before a
 *      single pixel is drawn.
 *   2. **RUNTIME MOUNT-FALLBACK, SECOND.** A renderer that fails to
 *      MOUNT (the vendor chunk is unreachable, the canvas cannot be
 *      acquired) drops to the fallback renderer — and the consumer is
 *      told, so it can SURFACE A BADGE. The registry never downgrades
 *      silently.
 *   3. **HARD ERROR, THIRD.** When there is NO fallback renderer for
 *      the request, the registry throws: a missing fallback is a
 *      defect, never a quiet degradation.
 *
 * ── THE REGISTRY IS MECHANISM, NOT ENGINES ──────────────────────────────
 *
 * This module imports NO engine. The house SVG's registration lives
 * beside the house SVG (`house-svg-renderer.renderer.ts`); the Chart.js
 * panel's lives beside the panel (`pages/metrics/charts/chart-panel.renderer.ts`).
 * The dependency direction therefore stays `pages → components` — the
 * tree the import-site rule expects.
 */

/** The intent vocabulary — WHAT the consumer wants drawn. */
export type ChartIntent = 'trend' | 'sparkline' | 'stat';

/** The capability flags — the shape the consumer needs. */
export type ChartFlag = 'shared-y' | 'markers' | 'bands' | 'drill';

/** The rendering surface — an interactive panel or a static shape. */
export type ChartSurface = 'interactive' | 'static';

/** The full intent vocabulary (the registry's coverage is checked against it). */
export const CHART_INTENTS: readonly ChartIntent[] = ['trend', 'sparkline', 'stat'];

/** The full flag vocabulary. */
export const CHART_FLAGS: readonly ChartFlag[] = ['shared-y', 'markers', 'bands', 'drill'];

/** The request — the intent plus the flags plus the optional surface. */
export interface ChartRequest {
  readonly intent: ChartIntent;
  readonly flags: readonly ChartFlag[];
  /**
   * The surface PREFERENCE.
   *
   * `undefined` means "the registry's order decides"; an explicit value
   * pins the family (the interactive panels vs the static shapes).
   */
  readonly surface?: ChartSurface;
}

/** What a renderer can do. */
export interface ChartCapabilities {
  readonly intents: readonly ChartIntent[];
  readonly flags: readonly ChartFlag[];
  readonly surface: ChartSurface;
  /** `unit` = a unit-labeled value axis; `time` = a UTC time axis; `none` = no axis. */
  readonly axis: 'unit' | 'time' | 'none';
  readonly interactive: boolean;
}

/** The data a renderer draws. */
export interface ChartRenderData {
  readonly series: readonly ChartSeries[];
  readonly anomalies?: readonly HouseAnomalyMarker[];
  readonly baseline?: readonly ChartSeries[];
  readonly unit?: string;
  readonly label?: string;
  /**
   * WHERE THE PANEL'S DATA ENDS (T1 phase 8, task 8.10).
   *
   * OPTIONAL and ADDITIVE — a renderer that does not draw a data-end
   * marker ignores it. The surface composes the marker (the house never
   * formats a time twice): `ms` is the epoch milliseconds of the LAST
   * plotted sample and `label` is the visible text beside the line.
   */
  readonly dataEnd?: { readonly ms: number; readonly label: string } | null;
}

/**
 * The point-click payload — the Chart.js panel's drill-down hand-off
 * (defined in the COMPONENTS layer so a renderer's `inputs()` can carry
 * it; pages import it from here).
 */
export interface ChartPointClick {
  /** Present only when the click landed on an anomaly marker. */
  readonly anomalyId?: string;
  readonly seriesId: string;
  /** Epoch MILLISECONDS. */
  readonly tsMs: number;
}

/**
 * The handlers a consumer offers to a renderer.
 *
 * A renderer takes the handler it needs as an INPUT and calls it; a
 * renderer with no matching handler binds nothing (a `null` callback),
 * so the two engines can share one hand-off shape.
 */
export interface ChartOutputHandlers {
  /** The house SVG's anomaly marker click. */
  readonly anomalyClick?: (marker: HouseAnomalyMarker) => void;
  /** The Chart.js panel's mount failure. */
  readonly mountFailed?: (reason: string) => void;
  /**
   * The Chart.js panel's point click — the drill-down hand-off (T1
   * phase 5, task 5.9: the resource timeline's marker click rides the
   * wrapper's `pointClick`).
   */
  readonly pointClick?: (payload: ChartPointClick) => void;
}

/**
 * The renderer PLAN — the declarative hand-off to an Angular consumer.
 *
 * The consumer renders the plan's component with the plan's inputs; the
 * registry owns WHICH component that is.
 */
export interface ChartRendererPlan {
  readonly rendererId: string;
  readonly component: Type<unknown>;
  /**
   * The component's INPUTS.
   *
   * The hand-off is INPUT-based on purpose: the consumer mounts the
   * component with `NgComponentOutlet`, and this Angular version's
   * `NgComponentOutlet` binds INPUTS only — it has no outputs binding.
   * The handlers therefore travel as inputs (the renderer calls the
   * callback it was given).
   */
  readonly inputs: (
    data: ChartRenderData,
    handlers?: ChartOutputHandlers,
  ) => Record<string, unknown>;
  readonly capabilities: ChartCapabilities;
}

/** A renderer — the registry's unit. */
export interface ChartRenderer {
  readonly id: string;
  readonly capabilities: ChartCapabilities;
  /** The plan for a request this renderer supports. */
  plan(data: ChartRenderData): ChartRendererPlan;
  /** Whether this renderer can serve the request. */
  supports(request: ChartRequest): boolean;
}

/** The registry's shape error — the build/test-time failure. */
export class ChartRegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ChartRegistryError';
  }
}

/** The unsupported-intent error — the loud failure at resolve time. */
export class UnsupportedChartIntentError extends Error {
  constructor(
    readonly intent: ChartIntent,
    readonly flags: readonly ChartFlag[],
  ) {
    super(
      `no renderer supports the intent '${intent}' with the flags [${flags.join(', ')}]`,
    );
    this.name = 'UnsupportedChartIntentError';
  }
}

/**
 * The registry.
 *
 * Resolution order: the renderers are consulted in registration order;
 * a renderer that supports the request wins. A request that names a
 * surface restricts the candidates to that surface.
 */
export class ChartRendererRegistry {
  private readonly renderers: ChartRenderer[] = [];

  /** Register a renderer (the registry line of the "add an engine" recipe). */
  register(renderer: ChartRenderer): void {
    this.renderers.push(renderer);
  }

  /** The registered renderers (read-only). */
  get registered(): readonly ChartRenderer[] {
    return this.renderers;
  }

  /** Whether ANY renderer can serve the request. */
  supports(request: ChartRequest): boolean {
    return this.renderers.some((renderer) => renderer.supports(request));
  }

  /**
   * Resolve a request to a renderer — LOUDLY.
   *
   * An intent nobody serves throws `UnsupportedChartIntentError`: the
   * registry never silently downgrades to "something that draws".
   */
  resolve(request: ChartRequest): ChartRenderer {
    const renderer = this.renderers.find((candidate) => candidate.supports(request));
    if (!renderer) {
      throw new UnsupportedChartIntentError(request.intent, request.flags);
    }
    return renderer;
  }

  /** The plan for a request (the declarative hand-off). */
  plan(request: ChartRequest, data: ChartRenderData): ChartRendererPlan {
    return this.resolve(request).plan(data);
  }

  /**
   * The FALLBACK plan — the runtime mount-fallback's second layer.
   *
   * The fallback is the static-surface renderer that supports the
   * request. When there is NONE, this THROWS: a missing fallback is a
   * defect (the third layer), never a quiet degradation.
   */
  fallbackPlan(request: ChartRequest, data: ChartRenderData): ChartRendererPlan {
    // The fallback renders the failed panel's SHAPE under the FALLBACK's
    // OWN rules (plan §4.2), so the probe carries the INTENT alone: a
    // capability the fallback cannot serve (the shared y-axis) is
    // dropped, not a reason to refuse the fallback.
    const fallback = this.renderers.find(
      (candidate) =>
        candidate.capabilities.surface === 'static' &&
        candidate.supports({ intent: request.intent, flags: [], surface: 'static' }),
    );
    if (!fallback) {
      throw new ChartRegistryError(
        `no fallback renderer supports the intent '${request.intent}' — a mount failure would be a silent downgrade`,
      );
    }
    return fallback.plan(data);
  }
}

/**
 * The BUILD/TEST-TIME VALIDATION (the first layer).
 *
 * It refuses a registry whose shape is broken:
 *
 *   * an intent in the REQUIRED set that NO renderer serves (a silent
 *     dead end — the consumer would ask for it at runtime). The
 *     required set defaults to the whole vocabulary; a registry that
 *     serves a subset (the static-only registry the resources route
 *     uses) declares the subset it owns;
 *   * a renderer whose `supports()` contradicts its own declared
 *     capabilities (the registry would advertise a capability the
 *     renderer does not have);
 *   * no static renderer at all (nothing could ever fall back).
 */
export function assertChartRegistryIsWellFormed(
  registry: ChartRendererRegistry,
  requiredIntents: readonly ChartIntent[] = CHART_INTENTS,
): void {
  if (registry.registered.length === 0) {
    throw new ChartRegistryError('the registry is EMPTY — no renderer is registered');
  }
  // The STRUCTURAL checks come first — they are the more specific
  // diagnosis, and the coverage check below would otherwise mask them.
  const hasFallback = registry.registered.some(
    (renderer) => renderer.capabilities.surface === 'static',
  );
  if (!hasFallback) {
    throw new ChartRegistryError(
      'no static renderer is registered — a mount failure could never fall back',
    );
  }
  for (const renderer of registry.registered) {
    for (const intent of renderer.capabilities.intents) {
      for (const flag of renderer.capabilities.flags) {
        const probe: ChartRequest = {
          intent,
          flags: [flag],
          surface: renderer.capabilities.surface,
        };
        if (!renderer.supports(probe)) {
          throw new ChartRegistryError(
            `the renderer '${renderer.id}' declares the intent '${intent}' and the flag '${flag}' but does not support them`,
          );
        }
      }
    }
  }
  for (const intent of requiredIntents) {
    const covered = registry.registered.some((renderer) =>
      renderer.supports({ intent, flags: [], surface: renderer.capabilities.surface }),
    );
    if (!covered) {
      throw new ChartRegistryError(`no renderer serves the intent '${intent}'`);
    }
  }
}

/**
 * The RUNTIME MOUNT-FALLBACK's outcome (the second layer).
 *
 * `fellBack: false` means the failure was a HARD ERROR (the third
 * layer) — and in that case this function THROWS instead of returning.
 */
export interface ChartMountFallback {
  readonly fellBack: true;
  /** The plan the consumer must render instead. */
  readonly plan: ChartRendererPlan;
  /** The badge the consumer must SURFACE — the downgrade is never silent. */
  readonly badge: string;
}

/**
 * The mount-failure hand-off.
 *
 * The consumer calls this when a renderer failed to MOUNT. The registry
 * hands back the FALLBACK plan plus the BADGE the consumer must show —
 * a downgrade without a badge is the failure mode the house forbids.
 *
 * When there is NO fallback renderer the call THROWS: the failure is a
 * hard error, never a quiet degradation.
 */
export function chartMountFallback(
  registry: ChartRendererRegistry,
  request: ChartRequest,
  data: ChartRenderData,
  reason: string,
): ChartMountFallback {
  const plan = registry.fallbackPlan(request, data);
  return {
    fellBack: true,
    plan,
    badge: `the interactive chart could not be mounted (${reason}) — the static rendering is shown instead`,
  };
}
