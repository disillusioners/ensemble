import { CommonModule } from '@angular/common';
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
} from '../../../models/resources.models';
import { ResourcesService } from '../../../services/resources.service';
import {
  DEFAULT_TIMELINE_SERIES,
  SvgTimelineComponent,
  TIMELINE_SERIES_DEFS,
  TimelineMarkerClick,
  TimelineSeriesKey,
  anomalyDrilldownWindow,
} from '../svg-timeline/svg-timeline.component';

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
    SvgTimelineComponent,
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
