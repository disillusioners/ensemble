# FE /metrics — chart primitive source excerpts

> Companion to `fe-metrics-source-audit.md`. Verbatim extracts so a reviewer can verify
> without re-reading the tree.

**Audited tree:** `/home/nea/ensemble-src-wt-stage-v0289/frontend/src/app/`
(HEAD `7fcf1bd71` = the v0.28.9 tag target; byte-identical to `origin/latest` for every
metrics FE file). **Line numbers are the ORIGINAL file line numbers**, not excerpt-local.

---

## 1. `components/charts/svg-line-chart/svg-line-chart.component.ts`

### 1.1 Header — the FEATURE-FROZEN declaration (lines 9-27)

```ts
/**
 * Dependency-free inline SVG chart primitive (P5 task 5.3 · D2 — the
 * zero-dependency guard; the house precedent is
 * `pages/resources/svg-timeline/svg-timeline.component.ts`: "no chart
 * library exists and v1 adds none").
 *
 * ONE shared primitive — never a per-page copy. It renders:
 *   - LINES and STEPS (a counter renders STEP: a linear interpolation
 *     between counter samples would assert values that were never
 *     observed — data honesty; the mode comes from the family
 *     metadata `chart_role`, contract §4.3);
 *   - AREAS (the fill under a line/step);
 *   - a LEGEND with each series' observed range;
 *   - native `<title>` tooltips (one per sample);
 *   - UTC axis labels (never local time).
 *
 * FEATURE-FROZEN: zoom / brush / crosshair / gradients are OUT (the
 * same closure `svg-timeline` declared).
 *
```

### 1.2 Geometry constants + the input types (lines 38-84)

```ts
// ── Geometry (fixed viewBox; the text is never stretched) ─────────────

export const CHART_VIEW_WIDTH = 900;
export const CHART_VIEW_HEIGHT = 260;
export const CHART_PLOT_LEFT = 56;
export const CHART_PLOT_RIGHT = CHART_VIEW_WIDTH - 16;
export const CHART_PLOT_TOP = 18;
export const CHART_PLOT_BOTTOM = CHART_VIEW_HEIGHT - 34;
export const CHART_PLOT_WIDTH = CHART_PLOT_RIGHT - CHART_PLOT_LEFT;
export const CHART_PLOT_HEIGHT = CHART_PLOT_BOTTOM - CHART_PLOT_TOP;
export const CHART_TICK_COUNT = 5;
export const CHART_POINT_RADIUS = 2.2;

/** One sample — `t` is epoch MILLISECONDS, `v` the observed value. */
export interface ChartPoint {
  readonly t: number;
  readonly v: number;
}

/** One series — the primitive's input shape. */
export interface ChartSeries {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  /** The rendering mode — `step` for counters, `line` for gauges. */
  readonly mode: MetricsChartRole;
  readonly points: readonly ChartPoint[];
}

export interface ChartCoord {
  readonly x: number;
  readonly y: number;
}

export interface ChartTick {
  readonly x: number;
  readonly label: string;
  readonly ms: number;
}

export interface ChartMarker {
  readonly key: string;
  readonly x: number;
  readonly y: number;
  readonly title: string;
}

```

### 1.3 Scale helpers — X and the PER-SERIES normalised Y (lines 107-132)

```ts
/** Time (epoch ms) → viewBox x. `t1 <= t0` degenerates to the midpoint. */
export function chartXScale(ms: number, t0: number, t1: number): number {
  if (!Number.isFinite(ms)) {
    return CHART_PLOT_LEFT;
  }
  if (!(t1 > t0)) {
    return CHART_PLOT_LEFT + CHART_PLOT_WIDTH / 2;
  }
  return CHART_PLOT_LEFT + clamp01((ms - t0) / (t1 - t0)) * CHART_PLOT_WIDTH;
}

/**
 * Value → viewBox y (inverted), NORMALISED against the series' own
 * observed [min,max]. A value below `min` clamps to the floor and a
 * value above `max` clamps to the ceiling — the axis never shows a
 * value that was not observed.
 */
export function chartYScale(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return CHART_PLOT_BOTTOM;
  }
  if (!(max > min)) {
    return CHART_PLOT_TOP + CHART_PLOT_HEIGHT / 2;
  }
  return CHART_PLOT_BOTTOM - clamp01((value - min) / (max - min)) * CHART_PLOT_HEIGHT;
}
```

### 1.4 Path builders — LINEAR (176-184) / STEP (186-213) / AREA FILL (215-229)

```ts
/** `M x0,y0 L x1,y1 …` — the empty string for an empty series. */
export function buildChartLinePath(coords: readonly ChartCoord[]): string {
  if (coords.length === 0) {
    return '';
  }
  return coords
    .map((coord, index) => `${index === 0 ? 'M' : 'L'}${coord.x.toFixed(2)},${coord.y.toFixed(2)}`)
    .join(' ');
}

/**
 * `M x0,y0 H x1 V y1 H x2 V y2 …` — the STEP path.
 *
 * The horizontal-then-vertical form is the step-after semantics: the
 * observed value HOLDS until the next sample. The `H`/`V` commands are
 * what make the step shape explicit (and assertable) — a linear path
 * has neither.
 */
export function buildChartStepPath(coords: readonly ChartCoord[]): string {
  if (coords.length === 0) {
    return '';
  }
  const first = coords[0];
  let path = `M${first.x.toFixed(2)},${first.y.toFixed(2)}`;
  for (let index = 1; index < coords.length; index += 1) {
    const coord = coords[index];
    path += ` H${coord.x.toFixed(2)} V${coord.y.toFixed(2)}`;
  }
  return path;
}

/** The path for a series in its own rendering mode. */
export function buildChartPath(
  coords: readonly ChartCoord[],
  mode: MetricsChartRole,
): string {
  return mode === 'step' ? buildChartStepPath(coords) : buildChartLinePath(coords);
}

/** The series path closed to the plot floor — the area fill. */
export function buildChartAreaPath(
  coords: readonly ChartCoord[],
  mode: MetricsChartRole,
): string {
  if (coords.length === 0) {
    return '';
  }
  const body = buildChartPath(coords, mode);
  const first = coords[0];
  const last = coords[coords.length - 1];
  return `${body} L${last.x.toFixed(2)},${CHART_PLOT_BOTTOM} L${first.x.toFixed(
    2,
  )},${CHART_PLOT_BOTTOM} Z`;
}
```

### 1.5 Tooltip text helper (lines 257-271)

```ts
/** A compact value label (integers stay integers). */
export function formatChartValue(value: number): string {
  if (!Number.isFinite(value)) {
    return '—';
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toFixed(2);
}

/** The native-tooltip text of a sample. */
export function chartPointTooltip(point: ChartPoint): string {
  return `${formatChartUtc(point.t)} UTC · ${formatChartValue(point.v)}`;
}
```

### 1.6 Template (a) — the STATIC legend with observed range (lines 280-292)

```html
    <div class="chart-legend" data-test="chart-legend">
      @for (entry of rendered(); track entry.id) {
        <span class="legend-item" [attr.data-series]="entry.id">
          <span class="legend-swatch" [style.background]="entry.color"></span>
          <span class="legend-label">{{ entry.label }}</span>
          @if (entry.sampleCount > 0) {
            <span class="legend-range"
              >{{ entry.minLabel }}–{{ entry.maxLabel }} ({{ entry.sampleCount }})</span
            >
          }
        </span>
      }
    </div>
```

### 1.6 Template (b) — VERTICAL-ONLY gridlines + x tick labels (lines 302-320)

```html
      <g class="chart-plot">
        @for (tick of ticks(); track tick.ms) {
          <line
            class="chart-grid"
            [attr.x1]="tick.x"
            [attr.x2]="tick.x"
            [attr.y1]="plotTop"
            [attr.y2]="plotBottom"
          ></line>
          <text
            class="chart-tick-label"
            [attr.x]="tick.x"
            [attr.y]="axisLabelY"
            text-anchor="middle"
            data-test="chart-tick"
          >
            {{ tick.label }}
          </text>
        }
```

### 1.6 Template (c) — area path, line path, per-point circles with native `<title>` (lines 322-353)

```html
        @for (entry of rendered(); track entry.id) {
          <path
            class="series-area"
            [attr.d]="entry.areaPath"
            [attr.fill]="entry.color"
            [attr.data-series]="entry.id"
            [attr.data-render-mode]="entry.mode"
            data-test="series-area"
          ></path>
          <path
            class="series-line"
            [attr.d]="entry.path"
            [attr.stroke]="entry.color"
            [attr.data-series]="entry.id"
            [attr.data-render-mode]="entry.mode"
            data-test="series-line"
          >
            <title>{{ entry.label }}</title>
          </path>
          @for (marker of entry.markers; track marker.key) {
            <circle
              class="series-point"
              [attr.cx]="marker.x"
              [attr.cy]="marker.y"
              [attr.r]="pointRadius"
              [attr.fill]="entry.color"
              [attr.data-series]="entry.id"
              data-test="chart-point"
            >
              <title>{{ marker.title }}</title>
            </circle>
          }
```

### 1.7 The component — TWO inputs, ZERO outputs (lines 441-456)

```ts
export class SvgLineChartComponent {
  /** The series to render. */
  readonly series = input<readonly ChartSeries[]>([]);

  /** The accessible name of the chart. */
  readonly ariaLabel = input<string>('Metric trend (UTC)');

  readonly viewBox = `0 0 ${CHART_VIEW_WIDTH} ${CHART_VIEW_HEIGHT}`;
  readonly plotLeft = CHART_PLOT_LEFT;
  readonly plotTop = CHART_PLOT_TOP;
  readonly plotBottom = CHART_PLOT_BOTTOM;
  readonly pointRadius = CHART_POINT_RADIUS;
  readonly plotMidX = CHART_PLOT_LEFT + CHART_PLOT_WIDTH / 2;
  readonly plotMidY = CHART_PLOT_TOP + CHART_PLOT_HEIGHT / 2;
  readonly axisLabelY = CHART_PLOT_BOTTOM + 18;
  readonly captionY = CHART_VIEW_HEIGHT - 4;
```

### 1.8 The per-series projection (lines 483-518)

```ts
  /** The per-series projection (paths, ranges, tooltip markers). */
  readonly rendered = computed<readonly ChartSeriesRender[]>(() => {
    const { t0, t1 } = this.timeDomain();
    return this.series().map((entry) => {
      const domain = chartSeriesDomain(entry.points);
      const coords = chartCoords(entry.points, t0, t1, domain.min, domain.max);
      const markers: ChartMarker[] = [];
      let index = 0;
      for (const point of entry.points) {
        if (!Number.isFinite(point.t) || !Number.isFinite(point.v)) {
          continue;
        }
        markers.push({
          key: `${entry.id}-${index}`,
          x: chartXScale(point.t, t0, t1),
          y: chartYScale(point.v, domain.min, domain.max),
          title: chartPointTooltip(point),
        });
        index += 1;
      }
      return {
        id: entry.id,
        label: entry.label,
        color: entry.color,
        mode: entry.mode,
        path: buildChartPath(coords, entry.mode),
        areaPath: buildChartAreaPath(coords, entry.mode),
        min: domain.min,
        max: domain.max,
        minLabel: formatChartValue(domain.min),
        maxLabel: formatChartValue(domain.max),
        sampleCount: coords.length,
        markers,
      };
    });
  });
```

---

## 2. `pages/metrics/charts/charts-tab.component.ts`

### 2.1 The HARDCODED window + poll cadence (lines 191-209)

```ts
  /** The poll cadence, in milliseconds (15 s — the door's cadence). */
  readonly POLL_INTERVAL_MS = 15_000;

  /** The window the tab asks for — 3600 s ⇒ 240 points at max_points 240. */
  readonly WINDOW_S = 3600;

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
```

### 2.2 The chart projection — ONE small multiple per returned series, color by INDEX (lines 216-234)

```ts
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
        color: CHART_COLORS[index % CHART_COLORS.length],
        mode,
        points: entry.points.map((point) => ({ t: point[0] * 1000, v: point[1] })),
      };
    });
  });
```

### 2.3 The chart grid template — `[series]="[chart]"` = exactly ONE series per chart (lines 108-121)

```html
      <div class="chart-grid" data-test="charts-grid">
        @for (chart of charts(); track chart.id) {
          <div class="chart-cell" [attr.data-series]="chart.id">
            <app-svg-line-chart
              [series]="[chart]"
              [ariaLabel]="chart.label + ' (UTC)'"
            ></app-svg-line-chart>
          </div>
        }
      </div>

      @if (payload() && charts().length === 0) {
        <p class="tab-hint" data-test="charts-empty">No samples in the window.</p>
      }
```

### 2.4 Error handling keeps the last-good payload + sets STALE (lines 282-315)

```ts
  private fetchTick(force = false): void {
    if (!force && this.inFlight > 0) {
      return;
    }
    this.inFlight += 1;
    const token = ++this.requestSeq;
    this.metrics
      .getSeries({
        names: this.chartSpecs().map((spec) => spec.name),
        windowS: this.WINDOW_S,
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
```

---

## 3. `pages/metrics/metrics.component.ts` — the chart roster does NOT filter DARK families (lines 157-175)

```ts
  /**
   * The chart-primary series — the roster's families (the catalog's
   * `role === 'chart'`) with their rendering mode. The door REJECTS an
   * over-ceiling selector, so the list is capped at the ceiling.
   */
  readonly chartSpecs = computed<readonly MetricsChartSeriesSpec[]>(() => {
    const out: MetricsChartSeriesSpec[] = [];
    for (const family of this.families()) {
      if (family.role !== 'chart') {
        continue;
      }
      for (const name of family.names) {
        const chartRole: MetricsChartRole =
          name.chart_role ?? metricsChartRoleForKind(name.kind);
        out.push({ name: name.name, chartRole });
      }
    }
    return out.slice(0, METRICS_SELECTOR_CEILING);
  });
```

---

## 4. For contrast — the OLDER resources chart HAS drill-down + anomaly markers

`pages/resources/svg-timeline/svg-timeline.component.ts:236-241` — the click handler that
`svg-line-chart` has no counterpart to:

```html
          <g
            class="anomaly-marker"
            data-test="anomaly-marker"
            [attr.data-anomaly-id]="marker.anomaly.id"
            (click)="onMarkerClick(marker.anomaly)"
          >
```

`svg-timeline.component.ts:386-392` — the anomaly input + the click OUTPUT:

```ts
  /** Anomaly markers from `GET /api/resources/anomalies`. */
  readonly anomalies = input<readonly Anomaly[]>([]);
  /** Enabled series keys (parent owns the toggle state). */
  readonly series = input<readonly TimelineSeriesKey[]>(DEFAULT_TIMELINE_SERIES);

  /** Emits the anomaly plus the padded `/traceback` window on marker click. */
  readonly markerClick = output<TimelineMarkerClick>();
```

`pages/resources/timeline/timeline-tab.component.ts:185-199` — the click → evidence +
traceback drill-down:

```ts
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
```
