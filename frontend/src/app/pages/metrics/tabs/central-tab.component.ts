import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { DecimalPipe } from '@angular/common';
import { NgComponentOutlet } from '@angular/common';

import { MetricsService } from '../../../services/metrics.service';
import {
  MetricsChartRole,
  MetricsFamily,
  MetricsSeriesEntry,
  MetricsSeriesPayload,
  MetricsSnapshotSeries,
  decodeMetricsDoorError,
  formatAvailabilityReason,
  formatMetricsLabels,
  formatMetricsValue,
  metricsChartRoleForKind,
} from '../../../models/metrics.model';
import {
  ChartSeries,
  formatChartUtcTick,
} from '../../../components/charts/house-svg-renderer/house-svg-renderer';
import { chartSeriesColor } from '../../../components/charts/chart-theme';
import {
  ChartMountFallback,
  ChartOutputHandlers,
  ChartRenderData,
  ChartRendererPlan,
  ChartRequest,
  chartMountFallback,
} from '../../../components/charts/renderer-registry';
import { createDefaultChartRegistry } from '../charts/chart-panel.renderer';
import {
  METRICS_UNIT_UNKNOWN,
  metricsPanelGroupKey,
  metricsUnitFor,
} from '../metrics-units';
import {
  METRICS_CENTRAL_WINDOW_MINUTES,
  METRICS_SELECTOR_CEILING,
} from '../metrics-door';
import {
  METRICS_BASELINE_CLAMPED_REASON,
  MetricsRangeOption,
  metricsCentralWindowSeconds,
  metricsDerivedIntervalS,
  metricsPayloadPointCount,
  metricsSeriesWindowS,
  splitSeriesAtMidpoint,
} from '../metrics-range';
import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from '../metrics-state';
import { MetricsCentralTab } from '../tabs.manifest';
import { MetricsDoorStateComponent } from '../metrics-door-state.component';

/**
 * One chart-primary series of a bound family — the rendering mode comes
 * from the WIRE (`chart_role`), never from an FE table.
 */
export interface MetricsCentralSeriesSpec {
  readonly name: string;
  readonly chartRole: MetricsChartRole;
  /** The family the series belongs to (the panel groups by family). */
  readonly familyId: string;
}

/**
 * One PANEL — a metric-kind + unit group of one family's series.
 *
 * A panel is never mixed-kind and never mixed-unit: the kind comes from
 * the wire and the unit from the vocabulary (IMPL-10).
 */
export interface MetricsPanelGroup {
  /** The group key — the kind + unit pair. */
  readonly id: string;
  readonly familyId: string;
  /** The wire's rendering mode — `step` for a counter, `line` for a gauge. */
  readonly kind: MetricsChartRole;
  /** The unit of the group (`unit unknown` when the vocabulary has none). */
  readonly unit: string;
  readonly label: string;
  readonly series: ChartSeries[];
}

/**
 * The RENDERER REQUEST the central panels carry.
 *
 * The flags name the capabilities the panels need: the shared y-axis
 * (one panel, several series), the anomaly markers and the drill-down.
 */
const PANEL_REQUEST: ChartRequest = {
  intent: 'trend',
  flags: ['shared-y', 'markers', 'drill'],
};

/**
 * The CENTRAL tab body — the shared implementation of the six central
 * module tabs (T1 phase 2, task 2.4).
 *
 * The body is deliberately THIN (the C-2 split):
 *
 *   * it BINDS its families (the manifest's binding, projected by the
 *     frame from the catalog);
 *   * it REQUESTS their series through the existing `MetricsService`;
 *   * it SURFACES their availability — a live family is charted, a dark
 *     family is LISTED with the reason the door gave (ADJ-vi). A dark
 *     family is never charted and never silently dropped.
 *
 * The PANEL COMPOSITION (task 4.15) IS here: the body groups its live
 * chart-primary series per METRIC-KIND + UNIT group and hands each
 * group to the RENDERER REGISTRY, which says which engine draws it.
 * The body renders THROUGH the registry — the resolved component is
 * mounted with `NgComponentOutlet`, never hard-coded — so adding an
 * engine later is one registry line and no change here.
 *
 * The state is PER SOURCE (AM-1): the body reports its own source's
 * outcome to the frame through `onState`, and it never writes another
 * tab's state.
 */
@Component({
  selector: 'app-metrics-central-tab',
  standalone: true,
  imports: [DecimalPipe, MatButtonModule, NgComponentOutlet, MetricsDoorStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="central-tab">
      <!--
        THIS TAB'S OWN FAILURE (task 8.3).

        The CENTRAL door's failure is rendered ONCE by the frame, so it
        is never published into this tab's error class — the panel below
        fires only for a failure of THIS tab's own read. The panel is
        the shared one, so the copy and the RETRY POLICY come from the
        same SSOT as the frame's.
      -->
      <app-metrics-door-state
        [code]="state().errorClass"
        [onRetry]="onRefresh"
        testId="central-tab-error"
        retryTestId="central-tab-retry"
      ></app-metrics-door-state>

      @if (!families().length) {
        <p class="tab-hint" data-test="central-tab-no-catalog">
          Waiting for the family catalog — {{ entry().label }} binds
          {{ familySummary() }}.
        </p>
      }

      <p class="tab-meta" data-test="central-tab-meta">
        @if (metaIntervalS(); as interval) {
          window {{ window() }} · {{ metaPointCount() }} points · effective spacing
          {{ interval | number: '1.0-1' }}s (derived from the payload)
        } @else {
          window {{ window() }} — no points yet
        }
      </p>

      @if (state().baselineRefusal; as refusal) {
        <p class="tab-hint" data-test="central-tab-baseline-refused">{{ refusal }}</p>
      }

      <div class="family-list" data-test="central-tab-families">
        @for (family of families(); track family.id) {
          @if (family.availability.state === 'live' && family.role === 'stat') {
            <section
              class="family family-stat"
              [attr.data-family]="family.id"
              data-test="central-tab-family-stat"
            >
              <header class="family-head">
                <span class="family-id">{{ family.id }}</span>
                <span class="pill pill-live" data-test="family-live">live</span>
                <span class="reason">stat family — the values are in the strip below</span>
              </header>
            </section>
          } @else if (family.availability.state === 'live') {
            <section
              class="family family-live"
              [attr.data-family]="family.id"
              data-test="central-tab-family-live"
            >
              <header class="family-head">
                <span class="family-id">{{ family.id }}</span>
                <span class="pill pill-live" data-test="family-live">live</span>
              </header>
              @for (group of groupsOf(family.id); track group.id) {
                <div
                  class="chart-cell"
                  [attr.data-panel]="group.id"
                  [attr.data-kind]="group.kind"
                  [attr.data-unit]="group.unit"
                  data-test="central-tab-panel"
                >
                  @if (fallbacks().get(group.id); as fallback) {
                    <span class="panel-fallback-badge" data-test="panel-fallback-badge">
                      {{ fallback.badge }}
                    </span>
                    <ng-container
                      *ngComponentOutlet="
                        fallback.plan.component;
                        inputs: fallback.plan.inputs(panelData(group))
                      "
                    ></ng-container>
                  } @else if (planFor(group.id); as plan) {
                    <ng-container
                      *ngComponentOutlet="
                        plan.component;
                        inputs: plan.inputs(panelData(group), handlersFor(group.id))
                      "
                    ></ng-container>
                  }
                </div>
              } @empty {
                <p class="tab-hint" data-test="central-tab-no-samples">
                  No samples in the window.
                </p>
              }
            </section>
          } @else {
            <section
              class="family family-dark"
              [attr.data-family]="family.id"
              [attr.data-reason]="family.availability.reason"
              data-test="central-tab-family-dark"
            >
              <header class="family-head">
                <span class="family-id">{{ family.id }}</span>
                <span class="pill pill-dark" data-test="family-dark">dark</span>
                <span class="reason">{{ reasonOf(family) }}</span>
              </header>
            </section>
          }
        }
      </div>

      @if (droppedSeriesCount() > 0) {
        <!--
          THE FE'S OWN TRUNCATION (task 8.8 — ADJ-vi).

          The FE slices the live series at the ceiling, so the series it
          drops are gone — there is NO "next page" to fetch them from.
          The old copy claimed one, which is a false statement about a
          page that does not exist. The count is stated, and the slice is
          named as this page's OWN.
        -->
        <p class="tab-hint" data-test="central-tab-selector-slice">
          {{ droppedSeriesCount() }} series are not shown — this page's own selector slice is
          {{ ceiling }}.
        </p>
      }

      @if (statCards().length) {
        <div class="stat-grid" data-test="central-tab-stat-strip">
          @for (card of statCards(); track card.key) {
            <div class="stat-card" [attr.data-series]="card.name" [attr.data-family]="card.family">
              <div class="stat-name">{{ card.name }}</div>
              @if (card.labels) {
                <div class="stat-labels">{{ card.labels }}</div>
              }
              <div class="stat-value">{{ card.value }}</div>
              <div class="stat-meta">
                {{ card.family }} · {{ card.kind }} · published
                {{ card.age | number: '1.0-1' }}s ago
              </div>
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .central-tab {
        display: block;
        padding: 0.75rem 0;
      }

      .tab-hint {
        color: #64748b;
        font-size: 0.8rem;
      }

      .tab-meta {
        margin: 0 0 0.5rem;
        color: #64748b;
        font-size: 0.72rem;
      }

      .family-list {
        display: block;
      }

      .family {
        border: 1px solid #1e293b;
        border-radius: 6px;
        padding: 0.5rem 0.6rem;
        margin-bottom: 0.6rem;
        background: #0f172a;
      }

      .family-head {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin-bottom: 0.35rem;
      }

      .family-id {
        color: #f1f5f9;
        font-size: 0.8rem;
        font-weight: 600;
      }

      .pill {
        display: inline-block;
        padding: 0.05rem 0.35rem;
        border-radius: 4px;
        font-size: 0.68rem;
      }

      .pill-live {
        border: 1px solid #14532d;
        background: #052e16;
        color: #4ade80;
      }

      .pill-dark {
        border: 1px solid #3f3f46;
        background: #18181b;
        color: #a1a1aa;
      }

      .reason {
        color: #fbbf24;
        font-size: 0.72rem;
      }

      .chart-cell {
        min-width: 0;
      }

      .panel-fallback-badge {
        display: inline-block;
        margin-bottom: 0.35rem;
        padding: 0.15rem 0.45rem;
        border: 1px solid #92400e;
        border-radius: 4px;
        color: #fbbf24;
        font-size: 0.7rem;
      }

      .stat-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
        gap: 0.6rem;
        margin-top: 0.75rem;
      }

      .stat-card {
        border: 1px solid #1e293b;
        border-radius: 6px;
        padding: 0.55rem 0.7rem;
        background: #0f172a;
      }

      .stat-name {
        font-size: 0.78rem;
        color: #cbd5e1;
        word-break: break-all;
      }

      .stat-labels {
        font-size: 0.7rem;
        color: #64748b;
        word-break: break-all;
      }

      .stat-value {
        font-size: 1.25rem;
        color: #f1f5f9;
        font-variant-numeric: tabular-nums;
      }

      .stat-meta {
        font-size: 0.68rem;
        color: #64748b;
      }
    `,
  ],
})
export class MetricsCentralTabComponent {
  /** The manifest entry of the module this body renders. */
  readonly entry = input.required<MetricsCentralTab>();

  /** This tab's state — written by its OWN source. */
  readonly state = input<MetricsTabState>(EMPTY_METRICS_TAB_STATE);

  /** The frame's per-tab state reporter. */
  readonly onState = input<MetricsTabStateReporter | null>(null);

  /**
   * The frame's window VALUE (the union) — the frame owns the window
   * (3.3): the tab body never hard-codes one.
   */
  readonly window = input<MetricsRangeOption>('1h');

  /**
   * Whether the previous-window comparison is ON (3.4).
   *
   * The baseline SHARES this tab's single series request: the request
   * carries 2W and the split happens client-side (3.10's invariant).
   */
  readonly baseline = input(false);

  /**
   * The frame's range-change reporter (AM-19).
   *
   * The central tabs never edit the range themselves — the frame's
   * control owns it — but the input is DECLARED so the frame's per-tab
   * input record binds cleanly on every slot.
   */
  readonly onRangeChange = input<(option: MetricsRangeOption) => void>(() => undefined);

  private readonly metrics = inject(MetricsService);
  private readonly destroyRef = inject(DestroyRef);

  /** The door's selector ceiling. */
  readonly ceiling = METRICS_SELECTOR_CEILING;

  /**
   * The window this tab asks for — the FRAME's value, mapped onto the
   * central ladder (3.3).
   *
   * The fallback is the ladder's own default: a custom-only option can
   * never reach a central tab (the frame snaps before it does).
   */
  readonly windowS = computed(() => {
    const seconds = metricsCentralWindowSeconds(this.window());
    return seconds ?? METRICS_CENTRAL_WINDOW_MINUTES['1h'] * 60;
  });

  /**
   * The `window_s` the request carries — 2W when the baseline is on, and
   * NEVER above the substrate ceiling (3.5b).
   */
  readonly requestWindowS = computed(() => metricsSeriesWindowS(this.windowS(), this.baseline()));

  /**
   * The EFFECTIVE point spacing, DERIVED from the payload (3.11 / AM-18).
   *
   * The door's `interval_s` is the OBSERVED sampler cadence; the plotted
   * points can be much further apart, so the meta line reports the spacing
   * of the points that are actually on screen.
   */
  readonly metaIntervalS = computed(() => metricsDerivedIntervalS(this.state().lastGood));

  /** The number of points the payload carries — the meta line's companion. */
  readonly metaPointCount = computed(() => metricsPayloadPointCount(this.state().lastGood));

  /** The stat-role series, keyed by the family they belong to. */
  private readonly statByFamily = signal<Readonly<Record<string, readonly MetricsSnapshotSeries[]>>>({});

  private requestSeq = 0;
  private statSeq = 0;
  private inFlight = 0;

  /**
   * The request GENERATION (AM-16 / 3.10).
   *
   * The supersede token bumps at request START, so it only discards a
   * response when a NEWER request has started. A range change or a
   * baseline toggle that starts no request would let an OLD-shape
   * response paint under the NEW label. The generation is bumped on every
   * such change — the in-flight response is then discarded even when no
   * new request followed it.
   */
  private requestGeneration = 0;

  /** The generation of the request that is currently in flight. */
  private inFlightGeneration = -1;

  /** The families this tab binds (projected by the frame). */
  readonly families = computed(() => this.state().families);

  /**
   * The chart-primary series of the LIVE bound families.
   *
   * The kind and the rendering mode come from the WIRE (`chart_role`),
   * never from an FE table — a family registering both a gauge and a
   * counter yields two series, each with its own mode.
   *
   * A STAT-primary family (the wire's `role`) contributes NO trend
   * series: it feeds the per-tab stat strip instead.
   */
  readonly chartSpecs = computed<readonly MetricsCentralSeriesSpec[]>(() => {
    const out: MetricsCentralSeriesSpec[] = [];
    for (const family of this.families()) {
      if (family.availability.state !== 'live') {
        continue;
      }
      if (family.role !== 'chart') {
        continue;
      }
      for (const name of family.names) {
        const chartRole: MetricsChartRole = name.chart_role ?? metricsChartRoleForKind(name.kind);
        out.push({ name: name.name, chartRole, familyId: family.id });
      }
    }
    return out.slice(0, METRICS_SELECTOR_CEILING);
  });

  /**
   * The EFFECT TRIGGER — the series NAMES as one string.
   *
   * A computed that returns a NEW array on every recompute would re-arm
   * the fetch effect on every state write (an endless request loop). A
   * string compares by VALUE, so the effect re-arms only when the
   * binding itself changes.
   */
  readonly chartSpecKey = computed(() =>
    this.chartSpecs()
      .map((spec) => spec.name)
      .join(','),
  );

  /**
   * The number of LIVE series the FE's OWN slice dropped (task 8.8 —
   * ADJ-vi).
   *
   * Only LIVE families count: a DARK family is filtered out BEFORE the
   * slice, so it never consumes a slot (a dark family has no series to
   * plot). The count is the number of series the page is NOT showing.
   */
  readonly droppedSeriesCount = computed(() => {
    let total = 0;
    for (const family of this.families()) {
      if (family.availability.state === 'live') {
        total += family.names.length;
      }
    }
    return Math.max(0, total - METRICS_SELECTOR_CEILING);
  });

  /** Whether the FE's own selector slice dropped series (never silent). */
  readonly selectorSliced = computed(() => this.droppedSeriesCount() > 0);

  /** The stat strip — one card per published stat series. */
  readonly statCards = computed(() => {
    const out: {
      key: string;
      family: string;
      name: string;
      labels: string;
      value: string;
      kind: string;
      age: number;
    }[] = [];
    for (const [family, series] of Object.entries(this.statByFamily())) {
      for (const entry of series) {
        const labels = formatMetricsLabels(entry.labels);
        out.push({
          key: `${family}:${entry.name}:${labels}`,
          family,
          name: entry.name,
          labels,
          value: formatMetricsValue(entry.value),
          kind: entry.kind,
          age: entry.last_publish_age_s,
        });
      }
    }
    return out;
  });

  constructor() {
    effect(() => {
      // The dependencies are read FIRST: a window or baseline change must
      // bump the generation even when no request is started (3.10).
      const key = this.chartSpecKey();
      const requestWindowS = this.requestWindowS();
      const baseline = this.baseline();
      this.requestGeneration += 1;
      if (!key) {
        return;
      }
      untracked(() => this.fetchTrend(requestWindowS, baseline));
    });

    effect(() => {
      const ids = this.entry().statFamilyIds ?? [];
      if (!ids.length) {
        return;
      }
      untracked(() => this.fetchStatStrip(ids));
    });
  }

  /**
   * The panel's retry handler — the user-initiated refresh (always
   * fires; it is never a silent no-op).
   */
  readonly onRefresh = (): void => {
    this.refresh();
  };

  /** The user-initiated refresh — always fires. */
  refresh(): void {
    this.fetchTrend(this.requestWindowS(), this.baseline());
    this.fetchStatStrip(this.entry().statFamilyIds ?? []);
  }

  /** The human reason of a dark family. */
  reasonOf(family: MetricsFamily): string {
    return formatAvailabilityReason(family.availability.reason);
  }

  /** The bound families as a readable list. */
  readonly familySummary = computed(() => this.entry().familyIds.join(', '));

  /**
   * The PANEL GROUPS of one family — one per metric-kind + unit group.
   *
   * The KIND comes from the WIRE (`chart_role` per metric — never an
   * FE table), so a family that registers both a gauge and a counter
   * yields TWO panels: a mixed-kind panel would break counter-honesty
   * (a counter rendered LINEAR asserts values that were never
   * observed) and a mixed-unit panel would falsify the unit label.
   *
   * The UNIT comes from the FE-local vocabulary, keyed on the metric
   * name (IMPL-10) — an unknown metric renders `unit unknown`, never a
   * borrowed unit.
   */
  readonly groupsByFamily = computed(() => {
    // The state's `series` is the CURRENT half: with the baseline on,
    // the payload covers 2W and the panels must show the recent window
    // only.
    const entries = this.state().series;
    const byFamily = new Map<string, Map<string, MetricsPanelGroup>>();
    if (!entries.length) {
      return byFamily;
    }
    const specs = this.chartSpecs();
    specs.forEach((spec, index) => {
      const entry = entries.find((candidate) => candidate.name === spec.name);
      if (!entry) {
        return;
      }
      const labels = formatMetricsLabels(entry.labels);
      const unit = metricsUnitFor(spec.name);
      const key = metricsPanelGroupKey(spec.chartRole, unit);
      const series: ChartSeries = {
        id: labels ? `${entry.name}{${labels}}` : entry.name,
        label: labels ? `${entry.name} {${labels}}` : entry.name,
        color: chartSeriesColor(index),
        mode: spec.chartRole,
        points: entry.points.map((point) => ({ t: point[0] * 1000, v: point[1] })),
      };
      let groups = byFamily.get(spec.familyId);
      if (!groups) {
        groups = new Map<string, MetricsPanelGroup>();
        byFamily.set(spec.familyId, groups);
      }
      const existing = groups.get(key);
      if (existing) {
        existing.series.push(series);
        return;
      }
      groups.set(key, {
        id: key,
        familyId: spec.familyId,
        kind: spec.chartRole,
        unit,
        label: `${spec.familyId} · ${spec.chartRole === 'step' ? 'counter' : 'gauge'}`,
        series: [series],
      });
    });
    return byFamily;
  });

  /** The panel groups of one family. */
  groupsOf(familyId: string): readonly MetricsPanelGroup[] {
    return [...(this.groupsByFamily().get(familyId)?.values() ?? [])];
  }

  /**
   * WHERE THE PANEL'S DATA ENDS (T1 phase 8, task 8.10).
   *
   * The LAST plotted sample of the panel — the newest point any of its
   * series carries. The marker is what tells a reader that a line which
   * STOPS is a line whose data ENDS, not a line that fell to zero.
   */
  readonly dataEndOf = (group: MetricsPanelGroup): { ms: number; label: string } | null => {
    let newest = Number.NEGATIVE_INFINITY;
    for (const series of group.series) {
      for (const point of series.points) {
        if (point.t > newest) {
          newest = point.t;
        }
      }
    }
    if (!Number.isFinite(newest)) {
      return null;
    }
    return { ms: newest, label: `data ends ${formatChartUtcTick(newest, 60_000)}` };
  };

  /** The render data of one group — what the renderer draws. */
  panelData(group: MetricsPanelGroup): ChartRenderData {
    return {
      series: group.series,
      anomalies: [],
      baseline: [],
      unit: group.unit === METRICS_UNIT_UNKNOWN ? METRICS_UNIT_UNKNOWN : group.unit,
      label: group.label,
      // The data-end marker (8.10).
      dataEnd: this.dataEndOf(group),
    };
  }

  /** The REGISTRY — the body renders THROUGH it, never around it. */
  private readonly registry = createDefaultChartRegistry();

  /**
   * The RENDERER PLAN of one group.
   *
   * The request is the INTERACTIVE panel: a unit-labeled value axis, the
   * crosshair + nearest-point snap, the legend's click-toggle and the
   * shared y-axis. The registry owns WHICH engine answers it.
   */
  private readonly planByGroup = computed(() => {
    const out = new Map<string, ChartRendererPlan>();
    for (const groups of this.groupsByFamily().values()) {
      for (const group of groups.values()) {
        out.set(group.id, this.registry.plan(PANEL_REQUEST, this.panelData(group)));
      }
    }
    return out;
  });

  /** The plan of one group (the template's lookup). */
  planFor(groupId: string): ChartRendererPlan | null {
    return this.planByGroup().get(groupId) ?? null;
  }

  /**
   * The MOUNT FAILURES — the runtime fallback's state.
   *
   * A panel that failed to MOUNT is replaced by the house SVG and the
   * downgrade is BADGED. A silent downgrade is the failure mode the
   * house forbids.
   */
  readonly fallbacks = signal<ReadonlyMap<string, ChartMountFallback>>(new Map());

  /** The handlers of one group (the mount-failure hand-off). */
  private readonly handlerCache = new Map<string, ChartOutputHandlers>();

  handlersFor(groupId: string): ChartOutputHandlers {
    const cached = this.handlerCache.get(groupId);
    if (cached) {
      return cached;
    }
    const handlers: ChartOutputHandlers = {
      mountFailed: (reason: string) => this.onMountFailed(groupId, reason),
    };
    this.handlerCache.set(groupId, handlers);
    return handlers;
  }

  /**
   * The mount failure → the house SVG fallback + the badge.
   *
   * The registry hands back the fallback plan and the badge text; the
   * body renders both. When there is NO fallback the registry THROWS —
   * a missing fallback is a defect, never a quiet degradation.
   */
  onMountFailed(groupId: string, reason: string): void {
    const groups = this.groupsByFamily();
    const group = [...groups.values()]
      .flatMap((entries) => [...entries.values()])
      .find((candidate) => candidate.id === groupId);
    if (!group) {
      return;
    }
    const fallback = chartMountFallback(
      this.registry,
      PANEL_REQUEST,
      this.panelData(group),
      reason,
    );
    this.fallbacks.update((current) => {
      const next = new Map(current);
      next.set(groupId, fallback);
      return next;
    });
  }

  private fetchTrend(requestWindowS: number, baseline: boolean): void {
    const generation = this.requestGeneration;
    if (this.inFlight > 0 && this.inFlightGeneration === generation) {
      // The in-flight request IS this generation — its answer is current,
      // so the skip-if-in-flight guard holds. A generation that MOVED (a
      // window or baseline change) never skips: the in-flight payload is
      // old-shape and must be replaced (3.10).
      return;
    }
    const specs = this.chartSpecs();
    if (!specs.length) {
      return;
    }
    this.inFlight += 1;
    this.inFlightGeneration = generation;
    const token = ++this.requestSeq;
    this.metrics
      .getSeries({ names: specs.map((spec) => spec.name), windowS: requestWindowS })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          this.inFlight -= 1;
          if (token !== this.requestSeq) {
            return;
          }
          if (generation !== this.requestGeneration) {
            // The window or the baseline moved while this request was in
            // flight — the payload is OLD-SHAPE and is discarded rather
            // than painted under the new label (3.10).
            return;
          }
          this.publishTrend(payload, baseline);
        },
        error: (err: unknown) => {
          this.inFlight -= 1;
          if (token !== this.requestSeq) {
            return;
          }
          if (generation !== this.requestGeneration) {
            return;
          }
          const decoded = decodeMetricsDoorError(err);
          // The last-good payload is RETAINED in every branch.
          this.onState()?.({ errorClass: decoded.code, doorState: decoded.code });
        },
      });
  }

  /**
   * Publish the trend — and the baseline's two halves (3.4).
   *
   * The split is SERVER-ANCHORED: it uses the RESPONSE's own window
   * anchors, never the client clock. A payload whose span is shorter than
   * 2W was CLAMPED by the door, so no baseline is published and the
   * refusal is stated (3.5c).
   */
  private publishTrend(payload: MetricsSeriesPayload, baseline: boolean): void {
    let series = payload.series;
    let baselineSeries: readonly MetricsSeriesEntry[] = [];
    let baselineRefusal: string | null = null;
    if (baseline) {
      const split = splitSeriesAtMidpoint(payload, this.windowS());
      if (split === null) {
        baselineRefusal = METRICS_BASELINE_CLAMPED_REASON;
      } else {
        series = split.current;
        baselineSeries = split.previous;
      }
    }
    this.onState()?.({
      series,
      baselineSeries,
      baselineRefusal,
      lastGood: payload,
      errorClass: null,
      doorState: 'ok',
    });
  }

  private fetchStatStrip(ids: readonly string[]): void {
    const token = ++this.statSeq;
    for (const family of ids) {
      this.metrics
        .getSnapshot({ family })
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          next: (payload) => {
            if (token !== this.statSeq) {
              return;
            }
            this.statByFamily.update((current) => ({ ...current, [family]: payload.series }));
          },
          error: () => {
            // A failed stat read never blanks the tab — the error is
            // swallowed and the strip keeps its last-known cards (a
            // family with no successful read yet simply stays absent).
            // The door's banner carries CATALOG-path failures only; it
            // does NOT speak for this snapshot-path failure.
            if (token !== this.statSeq) {
              return;
            }
          },
        });
    }
  }
}
