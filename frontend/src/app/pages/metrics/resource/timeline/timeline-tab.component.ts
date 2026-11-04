import { CommonModule, NgComponentOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { forkJoin } from 'rxjs';
import {
  Anomaly,
  EvidencePayload,
  HistoryPayload,
  TracebackPayload,
  formatUtcLabel,
  parseNaiveUtc,
} from '../../../../models/resources.models';
import { ResourcesService } from '../../../../services/resources.service';
import {
  ChartSeries,
  HouseAnomalyMarker,
} from '../../../../components/charts/house-svg-renderer/house-svg-renderer';
import {
  ChartOutputHandlers,
  ChartPointClick,
  ChartRenderData,
  ChartRendererPlan,
  ChartRequest,
  ChartMountFallback,
  chartMountFallback,
} from '../../../../components/charts/renderer-registry';
import { createDefaultChartRegistry } from '../../charts/chart-panel.renderer';
import {
  DEFAULT_TIMELINE_SERIES,
  TIMELINE_SERIES_DEFS,
  TimelineSeriesKey,
  resolveTimelineSeriesJoin,
  markerAnchorKey,
  TimelineSeriesJoin,
} from './timeline-series';
import { METRICS_UNIT_UNKNOWN, metricsUnitFor } from '../../metrics-units';
import { TimelineMarkerClick, anomalyDrilldownWindow } from './timeline-drilldown';
import { detectResourceDataGaps } from './data-gaps';
import { anomalyMarkerLabel } from './marker-label';

/**
 * Timeline PANEL — the history chart + anomaly markers + drill-down
 * (T1 phase 5, tasks 5.2/5.4: the panel is PRESENTATIONAL).
 *
 * ── WHAT CHANGED IN PHASE 5 (5.4) ─────────────────────────────────────
 *
 * The panel's own window-toggle UI is REMOVED. The window belongs to the
 * tab body (the module's own labelled "Timeline range" control — AM-19);
 * the body fetches the window's history + anomalies and hands BOTH down
 * here as inputs. The panel no longer fetches on its own, owns no window
 * state, and carries no window-ladder constant (the ladder's SSOT
 * is the frozen `ResourcesWindow` union + the manifest's custom entry —
 * the pinned residue check 5.13 greps this tree for exactly that
 * absence).
 *
 * What the panel STILL owns:
 *  - the series toggles (the parent-of-the-chart owns the toggles — the
 *    renderer draws exactly the series it is given);
 *  - the truncated banner (strictly from the frozen payload flag, D9);
 *  - the data-gap annotation strip (5.8 — derived from the history
 *    payload, never worded as restart/deploy/promote);
 *  - the DRILL-DOWN (D10): a marker click — via the Chart.js panel's
 *    `pointClick` (5.9) or the house SVG fallback's `anomalyClick`, or
 *    the anomalies panel's row click handed down through
 *    `openDrilldown` — runs `forkJoin({ evidence, traceback })` with the
 *    padded window and renders the panel below the chart. A monotonic
 *    supersede token (D-hardening) drops a stale drill-down response.
 *
 * ── THE SECOND RE-POINT (5.9 / AM-27) ─────────────────────────────────
 *
 * The timeline renders through the **PANEL ROLE** — the Chart.js wrapper
 * with the house anomaly overlay plugin — per plan-overview §4.2: the
 * enabled series split by their UNIT CLASS into up to THREE panels
 * (memory MB on a shared scale, CPU %, counts), each with a unit-labeled
 * y-axis. The anomaly markers ride as the house plugin (with the VISIBLE
 * API-word labels, 5.7) and the drill-down click rides the wrapper's
 * `pointClick`. A panel mount failure drops to the house SVG with a
 * BADGE — never a silent downgrade (the three-layer fallback).
 */

/** One unit-class panel of the timeline (plan-overview §4.2). */
interface TimelineUnitClassPanelSpec {
  readonly unitClass: 'memory' | 'cpu' | 'count';
  readonly title: string;
}

/** The three unit classes → three panels, in display order. */
const TIMELINE_UNIT_CLASS_PANELS: readonly TimelineUnitClassPanelSpec[] = [
  { unitClass: 'memory', title: 'Memory (MB)' },
  { unitClass: 'cpu', title: 'CPU (%)' },
  { unitClass: 'count', title: 'Counts' },
];

/**
 * The AXIS UNIT of a unit-class panel — read from the METRIC-NAME
 * vocabulary (phase 6, task 6.3 / IMPL-10), never from the class and
 * never guessed from the name shape. The class's FIRST series def is
 * the lookup key; every def in the class must map to the same unit
 * (the spec pins the agreement), and an unmapped class renders
 * `unit unknown` — never a guess.
 */
function timelinePanelUnit(unitClass: string): string {
  const def = TIMELINE_SERIES_DEFS.find((candidate) => candidate.unitClass === unitClass);
  return def ? metricsUnitFor(def.key) : METRICS_UNIT_UNKNOWN;
}

/** One rendered panel view — the plan the template mounts. */
export interface TimelinePanelView {
  readonly unitClass: string;
  readonly title: string;
  readonly plan: ChartRendererPlan;
}


@Component({
  selector: 'app-metrics-timeline',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule, NgComponentOutlet],
  templateUrl: './timeline-tab.component.html',
  styleUrl: './timeline-tab.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TimelineTabComponent {
  private readonly resources = inject(ResourcesService);
  private readonly destroyRef = inject(DestroyRef);

  // ── The body-owned data (5.4: the panel is presentational) ────────
  /** The window's history payload — `null` until the first good fetch. */
  readonly history = input<HistoryPayload | null>(null);
  /** The window's anomalies — the markers + the anomalies panel's rows. */
  readonly anomalies = input<readonly Anomaly[]>([]);
  /** True while the body is waiting for the FIRST good history fetch. */
  readonly loading = input(false);
  /** The body's history error branch — one line, last-good retained. */
  readonly error = input<string | null>(null);

  /** The body's retry — the panel's Retry CTA hands back to the owner. */
  readonly retry = output<void>();

  readonly seriesDefs = TIMELINE_SERIES_DEFS;

  /** Enabled series (the renderer is presentational — the panel owns the toggles). */
  readonly series = signal<readonly TimelineSeriesKey[]>(DEFAULT_TIMELINE_SERIES);

  // ── Drill-down state ─────────────────────────────────────────────
  readonly selectedAnomaly = signal<Anomaly | null>(null);
  readonly evidence = signal<EvidencePayload | null>(null);
  readonly traceback = signal<TracebackPayload | null>(null);
  readonly drilldownLoading = signal(false);
  readonly drilldownError = signal<string | null>(null);

  /**
   * Stale-response supersede token of the drill-down path. The window +
   * anomalies loads each own a token at the BODY (three request classes,
   * 5.11); the drill-down keeps its own token here because it runs
   * concurrently with the window loads.
   */
  private drilldownSeq = 0;

  readonly showError = computed(() => this.error() !== null);
  readonly points = computed(() => this.history()?.points ?? []);
  /** Render the banner strictly from the frozen payload flag (D9). */
  readonly truncated = computed(() => this.history()?.truncated === true);
  readonly resolutionUsed = computed(() => this.history()?.resolution_used ?? null);

  /** The data-gap annotations derived from the history payload (5.8). */
  readonly dataGaps = computed(() => detectResourceDataGaps(this.points()));

  onRetry(): void {
    this.retry.emit();
  }

  // ── The RENDERER REGISTRY — the SECOND RE-POINT (5.9 / AM-27) ─────

  /**
   * The DEFAULT registry — the Chart.js PANEL first, the house SVG
   * (mini + trend fallback) behind it. Phase 4 left this module on the
   * static registry; phase 5's 5.9 re-points the plan onto the panel
   * role: the resource timeline is the lane's first real interactive
   * panel (the anomaly overlay + the drill-down click + the baseline
   * capability).
   */
  private readonly registry = createDefaultChartRegistry();

  /**
   * The render request — the INTERACTIVE panel shape: the shared y-axis
   * (one panel per unit class, several series), the anomaly markers and
   * the drill-down.
   */
  private readonly request: ChartRequest = {
    intent: 'trend',
    flags: ['shared-y', 'markers', 'drill'],
    surface: 'interactive',
  };

  /**
   * The unit-class spec of a series key (the class travels with the
   * series).
   *
   * The key comes from the WIRE (`anomaly.series_key`), and the wire
   * carries TWO vocabularies (B-1): the history endpoint's FE-style
   * keys AND the anomalies endpoint's ENGINE keys (`daemon.rss_kb`,
   * …). The lookup therefore goes through the B-1 COMPATIBILITY SHIM
   * (`resolveTimelineSeriesJoin` — the typed table in
   * `timeline-series.ts`), which resolves both; an unknown key still
   * resolves to `null` and the marker joins NO panel (never a guess).
   */
  private unitClassOf(key: string): 'memory' | 'cpu' | 'count' | null {
    return resolveTimelineSeriesJoin(key)?.unitClass ?? null;
  }

  /** Whether a unit-class panel has any enabled series to draw. */
  private hasEnabledSeries(unitClass: string): boolean {
    return TIMELINE_SERIES_DEFS.some(
      (def) => def.unitClass === unitClass && this.series().includes(def.key),
    );
  }

  /** The series of ONE unit-class panel — the panel wrapper's shape. */
  private panelSeries(unitClass: string): ChartSeries[] {
    const points = this.points();
    return TIMELINE_SERIES_DEFS.filter(
      (def) => def.unitClass === unitClass && this.series().includes(def.key),
    ).map((def) => ({
      id: def.key,
      label: def.label,
      color: def.color,
      mode: 'line' as const,
      points: points
        .map((point) => ({
          t: parseNaiveUtc(point.t).getTime(),
          v: point[def.key],
        }))
        .filter(
          (sample): sample is { t: number; v: number } =>
            Number.isFinite(sample.t) && Number.isFinite(sample.v),
        ),
    }));
  }

  /**
   * The anomaly markers of ONE unit-class panel — the house plugin's
   * shape, with the VISIBLE API-word label composed per anomaly (5.7).
   *
   * B-1: the marker's `seriesId` is the FE DATASET id the overlay's
   * draw join (`houseDatasetIndex`) and the click join
   * (`houseNearestAnomaly`) both match on — so an ENGINE-keyed marker
   * resolves through the compatibility shim to its panel's dataset
   * (`markerAnchorKey`), while its LABEL keeps the API's engine words
   * verbatim (`anomalyMarkerLabel` reads the anomaly, never the join).
   */
  private panelAnomalies(unitClass: string): HouseAnomalyMarker[] {
    const enabled = this.series();
    return this.anomalies()
      .map((anomaly) => ({ anomaly, join: resolveTimelineSeriesJoin(anomaly.series_key) }))
      .filter(
        (entry): entry is { anomaly: Anomaly; join: TimelineSeriesJoin } =>
          entry.join !== null && entry.join.unitClass === unitClass,
      )
      .map(({ anomaly, join }) => ({
        anomalyId: anomaly.id,
        seriesId: markerAnchorKey(join.unitClass, enabled, join.anchorKey),
        tsMs: parseNaiveUtc(anomaly.t_start).getTime(),
        severity: anomaly.severity,
        title: anomalyMarkerLabel(anomaly),
      }));
  }

  /** The render data of ONE unit-class panel. */
  panelData(unitClass: string): ChartRenderData {
    const spec = TIMELINE_UNIT_CLASS_PANELS.find((entry) => entry.unitClass === unitClass);
    return {
      series: this.panelSeries(unitClass),
      anomalies: this.panelAnomalies(unitClass),
      baseline: [],
      // The axis unit comes from the METRIC-NAME vocabulary (6.3) — the
      // Chart.js wrapper suffixes it onto the shared value axis; the
      // house fallback labels its own axis with it.
      unit: timelinePanelUnit(unitClass),
      label: `${spec?.title ?? unitClass} — resource history (UTC)`,
    };
  }

  /**
   * The handlers of ONE unit-class panel: the wrapper's `pointClick`
   * (the drill-down hand-off, 5.9), the house SVG fallback's
   * `anomalyClick`, and the mount failure (the badge + fallback).
   */
  panelHandlers(unitClass: string): ChartOutputHandlers {
    return {
      pointClick: (payload: ChartPointClick) => this.onPanelPointClick(payload),
      anomalyClick: (marker: HouseAnomalyMarker) => this.onHouseMarkerClick(marker),
      mountFailed: (reason: string) => this.onMountFailed(unitClass, reason),
    };
  }

  /**
   * The rendered panels — the enabled unit classes, in display order,
   * resolved through the registry (the Chart.js panel wins a
   * `trend`+`interactive` request; the house SVG carries the fallback).
   */
  readonly panels = computed<readonly TimelinePanelView[]>(() => {
    const views: TimelinePanelView[] = [];
    for (const spec of TIMELINE_UNIT_CLASS_PANELS) {
      if (!this.hasEnabledSeries(spec.unitClass)) {
        continue;
      }
      views.push({
        unitClass: spec.unitClass,
        title: spec.title,
        plan: this.registry.plan(this.request, this.panelData(spec.unitClass)),
      });
    }
    return views;
  });

  /**
   * The MOUNT FAILURES — the runtime fallback's state, per unit class.
   * A failed Chart.js mount renders the house SVG with a BADGE (never a
   * silent downgrade); the registry THROWS when no fallback exists.
   */
  readonly fallbacks = signal<ReadonlyMap<string, ChartMountFallback>>(new Map());

  /** The mount failure → the house SVG fallback + the badge (5.9). */
  onMountFailed(unitClass: string, reason: string): void {
    const fallback = chartMountFallback(
      this.registry,
      this.request,
      this.panelData(unitClass),
      reason,
    );
    this.fallbacks.update((current) => {
      const next = new Map(current);
      next.set(unitClass, fallback);
      return next;
    });
  }

  /**
   * The timeline's series — the house shape of ALL enabled series
   * (kept for the fallback renderer's callers and the markers' labels).
   */
  readonly houseSeries = computed<ChartSeries[]>(() =>
    this.panelSeries('memory')
      .concat(this.panelSeries('cpu'))
      .concat(this.panelSeries('count')),
  );

  /**
   * The anomaly markers — the house shape with the VISIBLE label (5.7),
   * for ALL panels' worth of anomalies.
   */
  readonly houseAnomalies = computed<HouseAnomalyMarker[]>(() => [
    ...this.panelAnomalies('memory'),
    ...this.panelAnomalies('cpu'),
    ...this.panelAnomalies('count'),
  ]);

  /**
   * The wrapper's `pointClick` → the drill-down (5.9). Only a click that
   * landed on an anomaly marker opens the drill-down — a plain sample
   * click carries no id and is ignored (never a fabricated one).
   */
  onPanelPointClick(payload: ChartPointClick): void {
    if (!payload.anomalyId) {
      return;
    }
    const anomaly = this.anomalies().find((candidate) => candidate.id === payload.anomalyId);
    if (!anomaly) {
      return;
    }
    this.openDrilldown(anomaly);
  }

  /**
   * The house SVG fallback's marker click → the drill-down (the wire
   * anomaly is looked up by the marker's id; the padded window comes
   * from the drill-down POLICY, task 4.2).
   */
  onHouseMarkerClick(marker: HouseAnomalyMarker): void {
    const anomaly = this.anomalies().find((candidate) => candidate.id === marker.anomalyId);
    if (!anomaly) {
      return;
    }
    this.openDrilldown(anomaly);
  }

  // ── Series toggles ───────────────────────────────────────────────

  isSeriesEnabled(key: TimelineSeriesKey): boolean {
    return this.series().includes(key);
  }

  toggleSeries(key: TimelineSeriesKey): void {
    const enabled = this.series();
    this.series.set(
      enabled.includes(key) ? enabled.filter((k) => k !== key) : [...enabled, key],
    );
  }

  // ── Drill-down (D10 padded window from the primitive) ─────────────

  /**
   * Open the drill-down for an anomaly — the marker click's path AND
   * the anomalies panel's row click (5.5: the row click opens the
   * EXISTING drill-down; the body forwards the row here).
   */
  openDrilldown(anomaly: Anomaly): void {
    const { t0, t1 } = anomalyDrilldownWindow(anomaly);
    this.runDrilldown({ anomaly, t0, t1 });
  }

  private runDrilldown(click: TimelineMarkerClick): void {
    const token = ++this.drilldownSeq;
    this.selectedAnomaly.set(click.anomaly);
    this.evidence.set(null);
    this.traceback.set(null);
    this.drilldownError.set(null);
    this.drilldownLoading.set(true);

    forkJoin({
      evidence: this.resources.getEvidence(click.anomaly.id),
      traceback: this.resources.getTraceback(click.t0, click.t1),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ evidence, traceback }) => {
          if (token !== this.drilldownSeq) {
            return; // superseded by a newer drill-down/clear — stale never lands
          }
          this.drilldownLoading.set(false);
          this.evidence.set(evidence);
          this.traceback.set(traceback);
        },
        error: (err) => {
          if (token !== this.drilldownSeq) {
            return; // a newer fetch owns the outcome
          }
          this.drilldownLoading.set(false);
          this.drilldownError.set('Unable to load drill-down evidence for this window.');
          console.warn('[ResourcesTimeline] drill-down load failed:', err);
        },
      });
  }

  clearDrilldown(): void {
    // Invalidate any in-flight drill-down BEFORE clearing its state —
    // a late response must never repopulate a closed panel.
    this.drilldownSeq++;
    this.selectedAnomaly.set(null);
    this.evidence.set(null);
    this.traceback.set(null);
    this.drilldownError.set(null);
    this.drilldownLoading.set(false);
  }

  /** Retry the drill-down — re-derives the padded window from the anomaly. */
  retryDrilldown(): void {
    const anomaly = this.selectedAnomaly();
    if (anomaly === null) {
      return;
    }
    this.openDrilldown(anomaly);
  }

  // ── Display helpers ──────────────────────────────────────────────

  /** Naive-UTC wire timestamp → UTC clock label (D10). */
  utcLabel(value: string): string {
    return formatUtcLabel(value);
  }

  /** `chart_render` spawned children are the 8.3 GB-class signal. */
  isHotChildKind(kind: string): boolean {
    return kind === 'chart_render';
  }
}
