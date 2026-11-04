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
import { createHouseSvgRegistry } from '../../../../components/charts/house-svg-renderer/house-svg-renderer.renderer';
import {
  ChartOutputHandlers,
  ChartRenderData,
  ChartRequest,
} from '../../../../components/charts/renderer-registry';
import {
  DEFAULT_TIMELINE_SERIES,
  TIMELINE_SERIES_DEFS,
  TimelineSeriesKey,
} from './timeline-series';
import { TimelineMarkerClick, anomalyDrilldownWindow } from './timeline-drilldown';
import { detectResourceDataGaps } from './data-gaps';

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
 *  - the DRILL-DOWN (D10): a marker click — or the anomalies panel's
 *    row click handed down through `openDrilldown` — runs
 *    `forkJoin({ evidence, traceback })` with the padded window and
 *    renders the panel below the chart. A monotonic supersede token
 *    (D-hardening) drops a stale drill-down response.
 */

@Component({
  selector: 'app-resources-timeline',
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

  // ── The RENDERER REGISTRY (task 4.13) ─────────────────────────────

  /**
   * The STATIC registry — the house SVG only.
   *
   * Phase 5's 5.9 re-points this plan onto the Chart.js panel role in
   * its own commit (the AM-27 second re-point); this is the registry
   * Phase 4 left behind.
   */
  private readonly registry = createHouseSvgRegistry();

  /** The render request — the static trend shape with the markers + drill-down. */
  private readonly request: ChartRequest = {
    intent: 'trend',
    flags: ['markers', 'drill'],
    surface: 'static',
  };

  /** The renderer plan — the house SVG. */
  readonly plan = computed(() =>
    this.registry.plan(this.request, { series: [], anomalies: [] }),
  );

  /**
   * The timeline's series — the house shape.
   *
   * The series toggle stays here (the PARENT owns the state): the
   * renderer draws exactly the series it is given.
   */
  readonly houseSeries = computed<ChartSeries[]>(() => {
    const points = this.points();
    const enabled = this.series();
    return TIMELINE_SERIES_DEFS.filter((def) => enabled.includes(def.key)).map((def) => ({
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
  });

  /** The anomaly markers — the house shape (the wire anomaly rides along as the id). */
  readonly houseAnomalies = computed<HouseAnomalyMarker[]>(() =>
    this.anomalies().map((anomaly) => ({
      anomalyId: anomaly.id,
      seriesId: anomaly.series_key,
      tsMs: parseNaiveUtc(anomaly.t_start).getTime(),
      severity: anomaly.severity,
      title: `${anomaly.kind} · ${anomaly.shape} · ${anomaly.severity} · peak ${anomaly.peak}`,
    })),
  );

  /** The render data of the timeline. */
  timelineData(): ChartRenderData {
    return {
      series: this.houseSeries(),
      anomalies: this.houseAnomalies(),
      baseline: [],
      label: 'Resource history timeline (UTC)',
    };
  }

  /** The output handlers — the marker's click hands the anomaly over. */
  readonly timelineHandlers: ChartOutputHandlers = {
    anomalyClick: (marker: HouseAnomalyMarker) => this.onHouseMarkerClick(marker),
  };

  /** The `NgComponentOutlet` inputs for the resolved plan. */
  timelineInputs(): Record<string, unknown> {
    return this.plan().inputs(this.timelineData(), this.timelineHandlers);
  }

  /**
   * The marker click → the drill-down.
   *
   * The renderer hands over the MARKER; the wire anomaly behind it is
   * looked up by id, and the padded window comes from the drill-down
   * POLICY (which moved out of the chart engine in task 4.2).
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
