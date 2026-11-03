import { CommonModule, NgComponentOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { forkJoin } from 'rxjs';
import {
  Anomaly,
  EvidencePayload,
  HistoryPayload,
  ResourcesWindow,
  TracebackPayload,
  formatUtcLabel,
  parseNaiveUtc,
} from '../../../models/resources.models';
import { ResourcesService } from '../../../services/resources.service';
import {
  ChartSeries,
  HouseAnomalyMarker,
} from '../../../components/charts/house-svg-renderer/house-svg-renderer';
import { createHouseSvgRegistry } from '../../../components/charts/house-svg-renderer/house-svg-renderer.renderer';
import {
  ChartOutputHandlers,
  ChartRenderData,
  ChartRequest,
} from '../../../components/charts/renderer-registry';
import {
  DEFAULT_TIMELINE_SERIES,
  TIMELINE_SERIES_DEFS,
  TimelineSeriesKey,
} from './timeline-series';
import { TimelineMarkerClick, anomalyDrilldownWindow } from './timeline-drilldown';

/**
 * Timeline tab — history SVG + anomaly markers + marker-click drill-down.
 *
 * Semantics (D7 / D10, proven by `timeline-tab.component.spec.ts`):
 *  - `forkJoin({ history, anomalies })` on mount and on every window
 *    change — the two series stay for the SAME window (no skew).
 *  - Skeleton fires ONLY before the first successful fetch; a later
 *    window change retains the last-good chart while the new window
 *    loads (never a blank flash).
 *  - A failed load flips the SINGLE error branch (retry CTA).
 *  - A marker click runs `forkJoin({ evidence, traceback })` with the
 *    primitive's padded `t0`/`t1` (D10) and renders the drill-down
 *    panel below the chart.
 *  - Stale-response supersede (hardening pass): every fetch captures a
 *    monotonically increasing sequence token at start; a response that
 *    resolves after a NEWER fetch on the same path is DISCARDED, never
 *    allowed to overwrite newer state (window loads and drill-down
 *    each own a token — they run concurrently, so a marker click must
 *    not drop an in-flight window load and vice versa). A window
 *    change invalidates any open drill-down via `clearDrilldown()`.
 *  - The `truncated` banner is rendered strictly from the frozen
 *    payload flag — NO FE fallback logic (D9).
 */

/** Window selector order — frozen wire values (§2.8). */
export const TIMELINE_WINDOWS: readonly ResourcesWindow[] = ['15m', '1h', '24h', '7d'];

/** Plan T4 default. (The leader's dispatch mentioned 24h; the plan rules 1h.) */
export const DEFAULT_TIMELINE_WINDOW: ResourcesWindow = '1h';

@Component({
  selector: 'app-resources-timeline',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatIconModule,
    MatTooltipModule,
    NgComponentOutlet,
  ],
  templateUrl: './timeline-tab.component.html',
  styleUrl: './timeline-tab.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TimelineTabComponent implements OnInit {
  private readonly resources = inject(ResourcesService);
  private readonly destroyRef = inject(DestroyRef);

  readonly windows = TIMELINE_WINDOWS;
  readonly seriesDefs = TIMELINE_SERIES_DEFS;

  readonly window = signal<ResourcesWindow>(DEFAULT_TIMELINE_WINDOW);
  readonly history = signal<HistoryPayload | null>(null);
  readonly anomalies = signal<readonly Anomaly[]>([]);
  readonly error = signal<string | null>(null);

  /** Enabled series (the primitive is presentational — parent owns state). */
  readonly series = signal<readonly TimelineSeriesKey[]>(DEFAULT_TIMELINE_SERIES);

  // ── Drill-down state ─────────────────────────────────────────────
  readonly selectedAnomaly = signal<Anomaly | null>(null);
  readonly evidence = signal<EvidencePayload | null>(null);
  readonly traceback = signal<TracebackPayload | null>(null);
  readonly drilldownLoading = signal(false);
  readonly drilldownError = signal<string | null>(null);

  /**
   * Stale-response supersede tokens (one per fetch path). Monotonic;
   * a response whose captured token is no longer current is dropped.
   * Window loads and drill-downs each own a token because they run
   * concurrently — superseding one path must not drop the other.
   */
  private timelineSeq = 0;
  private drilldownSeq = 0;

  /** Skeleton renders only before the first successful fetch (D7). */
  readonly showSkeleton = computed(
    () => this.history() === null && this.error() === null,
  );
  readonly showError = computed(() => this.error() !== null);
  readonly points = computed(() => this.history()?.points ?? []);
  /** Render the banner strictly from the frozen payload flag (D9). */
  readonly truncated = computed(() => this.history()?.truncated === true);
  readonly resolutionUsed = computed(() => this.history()?.resolution_used ?? null);

  ngOnInit(): void {
    this.loadTimeline();
  }

  selectWindow(window: ResourcesWindow): void {
    if (window === this.window()) {
      return;
    }
    this.window.set(window);
    // A window change invalidates any open drill-down.
    this.clearDrilldown();
    this.loadTimeline();
  }

  onRetry(): void {
    this.loadTimeline();
  }

  /** `forkJoin({ history, anomalies })` for the active window (D6 shape). */
  private loadTimeline(): void {
    const window = this.window();
    const token = ++this.timelineSeq;
    this.error.set(null);
    forkJoin({
      history: this.resources.getHistory(window),
      anomalies: this.resources.getAnomalies(window),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ history, anomalies }) => {
          if (token !== this.timelineSeq) {
            return; // superseded by a newer load — stale payload never lands
          }
          this.history.set(history);
          this.anomalies.set(anomalies.anomalies);
        },
        error: (err) => {
          if (token !== this.timelineSeq) {
            return; // a newer load owns the outcome — no stale error flip
          }
          // Retain last-good chart; only the error branch flips.
          this.error.set(
            'Unable to load resource history — showing the last known window.',
          );
          console.warn('[ResourcesTimeline] timeline load failed:', err);
        },
      });
  }

  // ── The RENDERER REGISTRY (task 4.13) ─────────────────────────────

  /**
   * The STATIC registry — the house SVG only.
   *
   * The resources route never reaches the interactive vendor: its
   * timeline is a static shape, so the registry it uses carries no
   * Chart.js renderer at all.
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
    const { t0, t1 } = anomalyDrilldownWindow(anomaly);
    this.onMarkerClick({ anomaly, t0, t1 });
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

  // ── Marker click → drill-down (D10 padded window from the primitive) ─

  onMarkerClick(click: TimelineMarkerClick): void {
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
    const { t0, t1 } = anomalyDrilldownWindow(anomaly);
    this.onMarkerClick({ anomaly, t0, t1 });
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
