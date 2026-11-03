import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { DecimalPipe } from '@angular/common';
import { NgComponentOutlet } from '@angular/common';

import { MetricsService } from '../../../services/metrics.service';
import {
  MetricsChartRole,
  MetricsSeriesPayload,
  decodeMetricsDoorError,
  formatMetricsLabels,
  metricsChartRoleForKind,
} from '../../../models/metrics.model';
import { ChartSeries } from '../../../components/charts/house-svg-renderer/house-svg-renderer';
import { chartSeriesColor } from '../../../components/charts/chart-theme';
import {
  ChartRenderData,
  ChartRequest,
} from '../../../components/charts/renderer-registry';
import { createDefaultChartRegistry } from './chart-panel.renderer';
import { METRICS_CENTRAL_WINDOW_MINUTES } from '../metrics-door';

/** The rendering mode of one chart-primary series (from the catalog). */
export interface MetricsChartSeriesSpec {
  readonly name: string;
  readonly chartRole: MetricsChartRole;
}

/**
 * Charts tab — the windowed trend (`GET /api/metrics/series`).
 *
 * The tab renders the roster's CHART-PRIMARY families (the catalog's
 * `role === 'chart'` families) over the FRAME's window ⇒ 240 points at
 * the door's `max_points` default of 240 (15 s effective resolution
 * out of the box).
 *
 * Rendering: COUNTER series render STEP, GAUGE series render LINEAR —
 * the mode comes from the family metadata `chart_role` (contract
 * §4.3). A linear interpolation between counter samples would assert
 * values that were never observed.
 *
 * Polling discipline: the same contract as the Overview tab — a named
 * `POLL_INTERVAL_MS`, a `clearInterval` teardown, the visibility gate,
 * the skip-if-in-flight guard, the monotonic supersede token, one error
 * banner that retains the last-good view, and the STALE degradation.
 */
@Component({
  selector: 'app-metrics-charts',
  standalone: true,
  imports: [DecimalPipe, MatButtonModule, NgComponentOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="charts">
      @if (error(); as message) {
        <div class="tab-banner" data-test="charts-error">
          <span class="tab-banner-text">{{ message }}</span>
          <button mat-stroked-button type="button" (click)="refresh()" data-test="charts-retry">
            Retry
          </button>
        </div>
      }

      @if (stale()) {
        <span class="stale-badge" data-test="charts-stale">STALE — last known trend</span>
      }

      @if (!chartSpecs().length) {
        <p class="tab-hint" data-test="charts-no-catalog">
          Waiting for the family catalog — the chart-primary families drive this tab.
        </p>
      } @else if (!payload() && !error()) {
        <p class="tab-hint" data-test="charts-loading">Loading the trend…</p>
      }

      @if (payload(); as payload) {
        <p class="tab-meta" data-test="charts-meta">
          window {{ payload.window.from_utc }} → {{ payload.window.to_utc }} · interval
          {{ payload.window.interval_s | number: '1.0-1' }}s ({{ payload.window.interval_s_source }}) ·
          newest sample {{ payload.last_sample_age_s | number: '1.0-1' }}s old
          @if (payload.stale) {
            · door reports a stale trend
          }
          @if (payload.truncated) {
            · page bounded — some series were left out
          }
        </p>
      }

      <div class="chart-grid" data-test="charts-grid">
        @for (chart of charts(); track chart.id) {
          <div class="chart-cell" [attr.data-series]="chart.id">
            <ng-container
              *ngComponentOutlet="plan().component; inputs: plan().inputs(chartData(chart))"
            ></ng-container>
          </div>
        }
      </div>

      @if (payload() && charts().length === 0) {
        <p class="tab-hint" data-test="charts-empty">No samples in the window.</p>
      }
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .charts {
        display: block;
        padding: 0.75rem 0;
      }

      .tab-banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        padding: 0.5rem 0.75rem;
        margin-bottom: 0.6rem;
        border: 1px solid #7f1d1d;
        border-radius: 6px;
        background: #2a1414;
        color: #fca5a5;
        font-size: 0.8rem;
      }

      .stale-badge {
        display: inline-block;
        margin-bottom: 0.5rem;
        padding: 0.15rem 0.45rem;
        border: 1px solid #92400e;
        border-radius: 4px;
        color: #fbbf24;
        font-size: 0.72rem;
        letter-spacing: 0.04em;
      }

      .tab-hint {
        color: #64748b;
        font-size: 0.8rem;
      }

      .tab-meta {
        color: #64748b;
        font-size: 0.72rem;
        margin: 0 0 0.5rem;
      }

      .chart-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(24rem, 1fr));
        gap: 0.75rem;
      }

      .chart-cell {
        border: 1px solid #1e293b;
        border-radius: 6px;
        padding: 0.5rem 0.6rem;
        background: #0f172a;
        min-width: 0;
      }
    `,
  ],
})
export class ChartsTabComponent implements OnInit, OnDestroy {
  private readonly metrics = inject(MetricsService);
  private readonly destroyRef = inject(DestroyRef);

  /** The poll cadence, in milliseconds (15 s — the door's cadence). */
  readonly POLL_INTERVAL_MS = 15_000;

  /**
   * The window the tab asks for — the FRAME's value (task 4.13).
   *
   * The hardcoded window is GONE: the frame owns the range, and this
   * tab's ladder is the central one. The default is the central
   * ladder's own `1h` entry (in MINUTES, so no phase carries a
   * hardcoded seconds literal).
   */
  readonly windowS = input<number>(METRICS_CENTRAL_WINDOW_MINUTES['1h'] * 60);

  /** Whether THIS tab is the selected one (the shell's visibility gate). */
  readonly active = input(false);

  /** The chart-primary series, from the catalog (the shell's projection). */
  readonly chartSpecs = input<readonly MetricsChartSeriesSpec[]>([]);

  readonly payload = signal<MetricsSeriesPayload | null>(null);
  readonly error = signal<string | null>(null);
  readonly stale = signal(false);

  private readonly browserVisible = signal(true);
  private inFlight = 0;
  private requestSeq = 0;
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  private readonly onVisibilityChange = (): void => {
    this.browserVisible.set(document.visibilityState !== 'hidden');
  };

  /** The rendered charts — one small multiple per returned series. */
  readonly charts = computed<readonly ChartSeries[]>(() => {
    const payload = this.payload();
    if (!payload) {
      return [];
    }
    const modes = new Map(this.chartSpecs().map((spec) => [spec.name, spec.chartRole]));
    return payload.series.map((entry, index) => {
      const mode = modes.get(entry.name) ?? metricsChartRoleForKind(entry.kind);
      const labels = formatMetricsLabels(entry.labels);
      return {
        id: labels ? `${entry.name}{${labels}}` : entry.name,
        label: labels ? `${entry.name} {${labels}}` : entry.name,
        color: chartSeriesColor(index),
        mode,
        points: entry.points.map((point) => ({ t: point[0] * 1000, v: point[1] })),
      };
    });
  });

  /** The RENDERER REGISTRY — the tab renders THROUGH it, never around it. */
  private readonly registry = createDefaultChartRegistry();

  /**
   * The render request — the interactive panel with the shared y-axis,
   * the markers and the drill-down.
   */
  private readonly request: ChartRequest = {
    intent: 'trend',
    flags: ['shared-y', 'markers', 'drill'],
  };

  /** The renderer plan of the tab's panels. */
  readonly plan = computed(() => this.registry.plan(this.request, { series: [] }));

  /** The render data of one chart. */
  chartData(chart: ChartSeries): ChartRenderData {
    return { series: [chart], anomalies: [], baseline: [], label: chart.label };
  }

  constructor() {
    effect(() => {
      if (this.shouldPoll()) {
        this.startPolling();
      } else {
        this.stopPolling();
      }
    });
  }

  ngOnInit(): void {
    document.addEventListener('visibilitychange', this.onVisibilityChange);
  }

  ngOnDestroy(): void {
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    this.stopPolling();
  }

  /** The retry CTA — a user-initiated refresh always fires. */
  refresh(): void {
    this.fetchTick(true);
  }

  private shouldPoll(): boolean {
    // The tab polls ONLY while it is the selected tab, while the browser
    // tab is visible, and once the catalog has named the chart-primary
    // series — an empty name list would ask the door for EVERY series.
    return this.active() && this.browserVisible() && this.chartSpecs().length > 0;
  }

  private startPolling(): void {
    if (this.pollHandle !== null) {
      return;
    }
    this.fetchTick();
    this.pollHandle = setInterval(() => this.fetchTick(), this.POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  private fetchTick(force = false): void {
    if (!force && this.inFlight > 0) {
      return;
    }
    this.inFlight += 1;
    const token = ++this.requestSeq;
    this.metrics
      .getSeries({
        names: this.chartSpecs().map((spec) => spec.name),
        windowS: this.windowS(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          this.inFlight -= 1;
          if (token !== this.requestSeq) {
            return;
          }
          this.payload.set(payload);
          this.error.set(null);
          this.stale.set(payload.stale === true);
        },
        error: (err: unknown) => {
          this.inFlight -= 1;
          if (token !== this.requestSeq) {
            return;
          }
          // A-5: the copy branches on the decoded failure class; the
          // last-known trend is kept in EVERY branch.
          this.error.set(`${decodeMetricsDoorError(err).copy} — the last known trend is kept.`);
          this.stale.set(this.payload() !== null);
        },
      });
  }
}
