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
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';

import { MetricsDoorStateComponent } from '../metrics-door-state.component';
import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from '../metrics-state';
import { MetricsRangeOption } from '../metrics-range';
import { metricsTabById } from '../tabs.manifest';
import { ProjectService } from '../../../services/project.service';
import { Project } from '../../../models/project.model';
import { ResourcesWindow } from '../../../models/resources.models';
import { ToolUsageService } from '../../../services/tool-usage.service';
import {
  ToolUsageBanner,
  ToolUsageDailyPoint,
  ToolUsageDomainErrorCode,
  ToolUsageErrorCode,
  ToolUsageReusableErrorCode,
  ToolUsageRow,
  TOOL_USAGE_DOMAIN_COPY,
  decodeToolUsageError,
} from '../../../models/tool-usage.models';
import { HouseSvgRendererComponent } from '../../../components/charts/house-svg-renderer/house-svg-renderer.component';
import { ChartSeries } from '../../../components/charts/house-svg-renderer/house-svg-renderer';
import { chartSeriesColor } from '../../../components/charts/chart-theme';

/**
 * The TOOL-USAGE tab body (the tool-usage-metrics-p1 lane, phase 4 —
 * ENSEMBLE-37 / TokenEconomizer adoption, Phase 1 measurement slice).
 *
 * This is the HEAVY body: it is reached ONLY through the dynamic
 * `import()` inside `tool-usage-tab-host.component.ts` (the statically
 * imported thin host is what the shell's exact-cover map names), so
 * every byte here stays OUT of the `/metrics` route fetch set (B4b).
 *
 * ── THE SECTIONS (phase4-plan Task 2) ──────────────────────────────────
 *
 *   | section | source             | transport                     |
 *   |---------|--------------------|-------------------------------|
 *   | A       | project + window   | local UI state (drives reads) |
 *   | B       | /summary           | `ToolUsageService`            |
 *   | C       | row.agents         | none (already in the payload) |
 *   | D       | /daily             | `ToolUsageService` + house SVG|
 *   | E       | the route failure  | `MetricsDoorStateComponent`   |
 *
 * ── THE RANKING IS A PROXY (U14 — surfaced in the UI copy) ──────────────
 *
 * The FE `calls_7d` sort is a frequency-only PROXY for layer-2
 * candidacy — the Phase-3 daily digest (per-tool weight × frequency
 * where per-tool weights are published, zero/near-zero, and the
 * per-agent allow-list intersection) is the authoritative day-figure
 * and allow-list surface; while no per-tool weights are published it
 * ranks by frequency only — the same proxy as this table. calls 24h is
 * a rolling surface — the digest's D−1 complete-UTC-day window is the
 * precise day figure.
 *
 * The table carries that caveat verbatim. Note: there are NO per-tool
 * schema weights published today (every tool carries the 230-token
 * average fallback, so the Weight column is constant and the ranking is
 * frequency-only), and the allow-list intersection the copy names is now
 * computed from EFFECTIVE grants: registry categories are expanded, deny
 * wins, and runtime-swept names (MCP / `plane_`) render `unknown` rather
 * than a false yes/no — hence the withdrawn phrasing "is the authoritative
 * ranking surface" must not reappear. The FE's own `calls_7d` sort remains
 * a frequency-only proxy, not an authoritative WEIGHT ranking.
 *
 * ── THE D-7 DISCLOSURE ─────────────────────────────────────────────────
 *
 * `calls_24h` is a rolling today+ surface (vaguer by design); the daily
 * digest's D−1 complete-UTC-day window is the precise surface. The
 * column header says "calls 24h (rolling)" and never presents it as a
 * complete-day figure; the 7-day column says "calls (7d)" — never
 * "current" (R4).
 *
 * ── A1 — THE NEAR-ZERO THRESHOLD IS LOCAL ──────────────────────────────
 *
 * `NEAR_ZERO_CALLS_7D = 3` is a UI affordance of THIS component, NOT a
 * BE contract; it is never sent to the route (R5).
 *
 * ── THE WINDOW IS TAB-LOCAL (Section A, transport "none") ──────────────
 *
 * The window select is THIS tab's own labelled control: it initializes
 * from the frame's window value when the tab's own ladder can serve it
 * (the frame SNAPS to `7d` on entry — the measurement window) and maps
 * onto the BE's `days` parameter (`24h` → 1, `7d` → 7). It does NOT
 * round-trip through the frame's `onRangeChange` (AM-19's resource
 * pattern is deliberately not reused here: the slice's measurement
 * window is a tab-local choice).
 *
 * ── THE HELD-BUT-UNUSED INPUTS ─────────────────────────────────────────
 *
 * The shell passes the SAME five inputs to every tab. `state` /
 * `onState` / `baseline` / `onRangeChange` are declared and held: the
 * custom slot renders its OWN source's state (AM-1 — the frame never
 * writes a custom slot's state slot), the baseline is a central-only
 * capability (AM-14), and the window control above is tab-local.
 * Undeclared inputs THROW in dev — every tab declares all five.
 *
 * ── THE SINGLE-BANNER DISCIPLINE ───────────────────────────────────────
 *
 * ONE banner per failing SOURCE (the summary and the daily series are
 * two sources), and the AUTH-POSTURE classes are stated ONCE: when the
 * summary banner carries a posture class, the trend section suppresses
 * its own banner (a one-line hint instead) — under the auth-off live
 * posture the tab renders exactly ONE fail-closed banner, no Retry.
 * The empty state is DISTINCT from every error state: a failed summary
 * never renders the "no tool usage recorded" hint (the failure is
 * stated, never dressed as an empty ledger).
 *
 * ── THE TEMPLATE IS SAFE (AM-12d precedent) ────────────────────────────
 *
 * No `innerHTML` and no `bypassSecurityTrust` — every wire string is
 * rendered as TEXT (the tab pack pins the property).
 */
@Component({
  selector: 'app-metrics-tool-usage-tab',
  standalone: true,
  imports: [MatButtonModule, MetricsDoorStateComponent, HouseSvgRendererComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="tool-usage" data-test="tool-usage-section">
      <h3 class="tool-usage-title">
        Tool usage
        <span class="tab-hint">
          the layer-2 measurement surface — high-frequency vs zero/near-zero per tool
        </span>
        @if (generatedAt(); as instant) {
          <span class="tab-hint" data-test="tool-usage-generated-at">
            as of {{ instant }} (server clock — never the client clock)
          </span>
        }
      </h3>

      <div class="tool-usage-controls">
        <label class="tool-usage-control">
          <span class="tool-usage-control-label">Project</span>
          <select
            class="tool-usage-select"
            data-test="tool-usage-project-select"
            [value]="projectId()"
            (change)="onProjectChange($any($event.target).value)"
          >
            <option value="">All projects</option>
            @for (project of projects(); track project.project_id) {
              <option [value]="project.project_id">{{ project.name }}</option>
            }
          </select>
        </label>
        <label class="tool-usage-control">
          <span class="tool-usage-control-label">Window</span>
          <select
            class="tool-usage-select"
            data-test="tool-usage-window-select"
            [value]="windowChoice()"
            (change)="onWindowChange($any($event.target).value)"
          >
            @for (option of ladder; track option) {
              <option [value]="option">{{ option }}</option>
            }
          </select>
        </label>
      </div>

      <!--
        SECTION E — the summary source's banner (ONE per failing source;
        no Retry on the auth-off posture — the door copy is data-driven).
      -->
      @if (summaryBanner(); as banner) {
        @if (banner.kind === 'domain') {
          <div
            class="tool-usage-error"
            data-test="tool-usage-error"
            [attr.data-error-class]="banner.code"
          >
            <span data-test="tool-usage-error-cause">{{
              domainCopy(banner.code).cause +
              ' ' +
              domainCopy(banner.code).meaning +
              ' ' +
              domainCopy(banner.code).action
            }}</span>
            @if (domainCopy(banner.code).retryable) {
              <button
                mat-stroked-button
                type="button"
                (click)="retrySummary()"
                data-test="tool-usage-retry"
              >
                Retry
              </button>
            }
          </div>
        } @else {
          <app-metrics-door-state
            [code]="banner.code"
            [onRetry]="retrySummary"
            testId="tool-usage-error"
            retryTestId="tool-usage-retry"
          ></app-metrics-door-state>
        }
      }

      @if (sortedRows(); as rowsList) {
        @if (rowsList.length === 0) {
          <!-- The EMPTY state — DISTINCT from every error state (a failed
               read never lands here: sortedRows stays null on a 503). -->
          <p class="tab-hint" data-test="tool-usage-empty">
            no tool usage recorded in the window
          </p>
        } @else {
          <p class="tab-hint" data-test="tool-usage-ranking-caveat">
            the calls (7d) sort is a frequency-only PROXY for layer-2 candidacy — the
            Phase-3 daily digest (per-tool weight × frequency where per-tool weights are
            published, zero/near-zero, and the per-agent allow-list intersection) is the
            authoritative day-figure and allow-list surface; while no per-tool weights are
            published it ranks by frequency only — the same proxy as this table. calls 24h
            is a rolling surface — the digest's D−1 complete-UTC-day window is the precise
            day figure.
          </p>
          <table class="tool-usage-table" data-test="tool-usage-table">
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">Category</th>
                <th scope="col">calls 24h (rolling)</th>
                <th scope="col">calls (7d)</th>
                <th scope="col">calls total</th>
                <th scope="col">agents</th>
                <th scope="col">layer-2</th>
              </tr>
            </thead>
            <tbody>
              @for (row of rowsList; track row.tool_name) {
                <tr
                  class="tool-usage-row"
                  [class.tool-usage-row-zero]="isNearZero(row)"
                  [attr.data-test]="'tool-usage-row-' + row.tool_name"
                  (click)="toggleRow(row.tool_name)"
                >
                  <td>{{ row.tool_name }}</td>
                  <td>{{ row.tool_category }}</td>
                  <td>{{ row.calls_24h }}</td>
                  <td>{{ row.calls_7d }}</td>
                  <td>{{ row.calls_total }}</td>
                  <td>{{ row.agents.length }}</td>
                  <td>
                    @if (isNearZero(row)) {
                      <span class="badge badge-zero" data-test="tool-usage-zero-badge">
                        ≈0 calls/7d
                      </span>
                      <span class="badge badge-candidate" data-test="tool-usage-candidate-badge">
                        layer-2 candidate
                      </span>
                    }
                  </td>
                </tr>
                <!-- SECTION C — the per-agent breakdown (row.agents, NO second fetch). -->
                @if (selectedTool() === row.tool_name) {
                  <tr data-test="tool-usage-agent-breakdown">
                    <td colspan="7">
                      <div class="tool-usage-breakdown">
                        <span class="tab-hint">
                          calls (7d) per agent — a 7-day aggregate, never a current severity
                        </span>
                        <div class="agent-list">
                          @for (agent of row.agents; track agent.agent_id) {
                            <span class="agent-cell">
                              {{ agent.agent_id }} · {{ agent.calls_7d }}
                            </span>
                          }
                          @if (row.agents.length === 0) {
                            <span class="tab-hint">no attributed agent in the window</span>
                          }
                        </div>
                      </div>
                    </td>
                  </tr>
                }
              }
            </tbody>
          </table>
        }
      }

      <!-- SECTION D — the trend chart (one series for the SELECTED tool). -->
      <div class="tool-usage-trend">
        @if (selectedTool(); as tool) {
          <h4 class="tool-usage-trend-title">daily calls — {{ tool }}</h4>
          @if (trendSuppressed(); as note) {
            <p class="tab-hint">{{ note }}</p>
          } @else {
            @if (dailyBanner(); as banner) {
              @if (banner.kind === 'domain') {
                <div
                  class="tool-usage-error"
                  data-test="tool-usage-trend-error"
                  [attr.data-error-class]="banner.code"
                >
                  <span data-test="tool-usage-trend-error-cause">{{
                    domainCopy(banner.code).cause +
                    ' ' +
                    domainCopy(banner.code).meaning +
                    ' ' +
                    domainCopy(banner.code).action
                  }}</span>
                  @if (domainCopy(banner.code).retryable) {
                    <button
                      mat-stroked-button
                      type="button"
                      (click)="retryDaily()"
                      data-test="tool-usage-trend-retry"
                    >
                      Retry
                    </button>
                  }
                </div>
              } @else {
                <app-metrics-door-state
                  [code]="banner.code"
                  [onRetry]="retryDaily"
                  testId="tool-usage-trend-error"
                  retryTestId="tool-usage-trend-retry"
                ></app-metrics-door-state>
              }
            }
            @if (daily(); as points) {
              <div class="tool-usage-trend-chart" data-test="tool-usage-trend">
                <app-house-svg-renderer
                  [variant]="'trend'"
                  [series]="trendSeries()"
                  [unit]="'calls'"
                  [ariaLabel]="'Tool usage, calls per day (UTC)'"
                ></app-house-svg-renderer>
              </div>
            }
          }
        } @else {
          <p class="tab-hint">select a tool in the table for its daily trend</p>
        }
      </div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .tab-hint {
        color: #64748b;
        font-size: 0.8rem;
      }

      .tool-usage-title {
        margin: 0 0 0.5rem;
        font-size: 1rem;
      }

      .tool-usage-controls {
        display: flex;
        flex-wrap: wrap;
        gap: 1rem;
        margin-bottom: 0.75rem;
      }

      .tool-usage-control {
        display: flex;
        align-items: center;
        gap: 0.4rem;
      }

      .tool-usage-control-label {
        color: #64748b;
        font-size: 0.8rem;
      }

      .tool-usage-select {
        background: inherit;
        color: inherit;
        border: 1px solid #334155;
        border-radius: 6px;
        padding: 0.15rem 0.35rem;
        font-size: 0.8rem;
      }

      .tool-usage-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.8rem;
      }

      .tool-usage-row {
        cursor: pointer;
      }

      .tool-usage-row-zero {
        background: #131f2e;
      }

      .tool-usage-row-zero td:first-child {
        border-left: 2px solid #fbbf24;
      }

      .badge {
        display: inline-block;
        border-radius: 6px;
        border: 1px solid #334155;
        padding: 0 0.35rem;
        font-size: 0.72rem;
        margin-right: 0.25rem;
      }

      .badge-zero {
        color: #94a3b8;
      }

      .badge-candidate {
        color: #fbbf24;
        border-color: #fbbf24;
      }

      .tool-usage-error {
        border: 1px solid #7f1d1d;
        border-radius: 6px;
        color: #fca5a5;
        padding: 0.5rem 0.75rem;
        margin-bottom: 0.6rem;
        font-size: 0.8rem;
      }

      .tool-usage-breakdown {
        padding: 0.35rem 0;
      }

      .agent-list {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-top: 0.25rem;
      }

      .agent-cell {
        font-family: monospace;
        font-size: 0.72rem;
        color: #94a3b8;
      }

      .tool-usage-trend {
        margin-top: 0.9rem;
      }

      .tool-usage-trend-title {
        margin: 0 0 0.35rem;
        font-size: 0.9rem;
      }

      .tool-usage-trend-chart {
        display: block;
      }
    `,
  ],
})
export class ToolUsageTabComponent implements OnInit, OnDestroy {
  // ── THE FIVE SHELL INPUTS (all declared — undeclared inputs THROW) ────

  /** The frame's per-tab state — HELD (the custom slot's state is its own, AM-1). */
  readonly state = input<MetricsTabState>(EMPTY_METRICS_TAB_STATE);

  /** The frame's state reporter — HELD (this body reports its own source locally). */
  readonly onState = input<MetricsTabStateReporter | null>(null);

  /** The frame's window VALUE — read ONCE at init as the select's seed. */
  readonly window = input<MetricsRangeOption>('7d');

  /** The baseline flag — HELD (the baseline is a central-tabs-only capability, AM-14). */
  readonly baseline = input<boolean>(false);

  /** The frame's range-change reporter — HELD (the window control is tab-local). */
  readonly onRangeChange = input<(option: MetricsRangeOption) => void>(() => undefined);

  /** The manifest entry — this tab's OWN ladder (`['24h', '7d']`). */
  readonly entry = metricsTabById('tool-usage');
  readonly ladder = (this.entry?.allowedWindows ?? ['7d']) as readonly ResourcesWindow[];

  /**
   * A1 — the near-zero threshold: a LOCAL UI affordance of this
   * component, NOT a BE contract; never sent to the route (R5).
   */
  static readonly NEAR_ZERO_CALLS_7D = 3;

  /** The summary poll cadence — the house 15 s budget. */
  static readonly POLL_INTERVAL_MS = 15_000;

  /**
   * The window → `days` mapping (the BE's `days` param): the tab's own
   * ladder mapped onto whole UTC days. `7d` is the measurement window
   * (matches the BE's `days=7` default and the commission's ~7-day
   * readout); `24h` maps to 1 day.
   */
  static readonly WINDOW_DAYS: Readonly<Record<string, number>> = {
    '24h': 1,
    '7d': 7,
  };

  /** The auth-posture classes — stated ONCE by the summary banner. */
  static readonly POSTURE_CLASSES: readonly ToolUsageErrorCode[] = [
    'auth_not_configured',
    'scope_insufficient',
    'auth_required',
  ];

  private readonly toolUsage = inject(ToolUsageService);
  private readonly projectService = inject(ProjectService);
  private readonly destroyRef = inject(DestroyRef);

  // ── SECTION A — the tab-local controls ─────────────────────────────────

  /** The projects for the selector (A5 — the existing ProjectService). */
  readonly projects = signal<readonly Project[]>([]);

  /** `''` = All projects — the param is OMITTED (the frozen all-projects semantics). */
  readonly projectId = signal<string>('');

  /** The tab-local window — seeded from the frame's value when servable. */
  readonly windowChoice = signal<ResourcesWindow>(this.seedWindow());

  // ── SECTION B — the summary source's state ─────────────────────────────

  /** The rows — `null` until the FIRST good answer (a failure never fakes one). */
  readonly rows = signal<readonly ToolUsageRow[] | null>(null);

  /** The summary's failure class (decoded). */
  readonly summaryError = signal<ToolUsageErrorCode | null>(null);

  /** The SERVER's instant — rendered, never the client clock. */
  readonly generatedAt = signal<string | null>(null);

  // ── SECTIONS C + D — the selection, the breakdown, the trend ───────────

  /** The selected tool (ONE at a time — drives the breakdown AND the trend). */
  readonly selectedTool = signal<string | null>(null);

  /** The daily series of the selected tool — `null` until it answers. */
  readonly daily = signal<readonly ToolUsageDailyPoint[] | null>(null);

  /** The daily read's failure class (the 404 is a DOMAIN outcome). */
  readonly dailyError = signal<ToolUsageErrorCode | null>(null);

  // ── The supersede tokens — ONE per request CLASS (summary, daily) ──────

  private summarySeq = 0;
  private dailySeq = 0;
  private summaryInFlight = false;
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private browserVisible = true;

  /** The rows sorted for DISPLAY — `calls_7d` DESC, ties by tool name asc. */
  readonly sortedRows = computed<readonly ToolUsageRow[] | null>(() => {
    const current = this.rows();
    if (current === null) {
      return null;
    }
    return [...current].sort(
      (a, b) => b.calls_7d - a.calls_7d || a.tool_name.localeCompare(b.tool_name),
    );
  });

  /**
   * The summary source's banner — the DISCRIMINATED shape (the shared
   * `app-metrics-door-state` accepts a DOOR class only; the domain-only
   * classes are not door classes).
   */
  readonly summaryBanner = computed<ToolUsageBanner | null>(() => {
    const code = this.summaryError();
    if (!code) {
      return null;
    }
    return code in TOOL_USAGE_DOMAIN_COPY
      ? { kind: 'domain', code: code as ToolUsageDomainErrorCode }
      : { kind: 'door', code: code as ToolUsageReusableErrorCode };
  });

  /** Whether the trend section's banner is suppressed (the posture stated once). */
  readonly trendSuppressed = computed<string | null>(() => {
    const summary = this.summaryError();
    if (summary !== null && ToolUsageTabComponent.POSTURE_CLASSES.includes(summary)) {
      return "the daily trend is unavailable under this daemon's auth posture.";
    }
    return null;
  });

  /** The daily source's banner — suppressed under the summary's posture. */
  readonly dailyBanner = computed<ToolUsageBanner | null>(() => {
    const code = this.dailyError();
    if (!code || this.trendSuppressed() !== null) {
      return null;
    }
    return code in TOOL_USAGE_DOMAIN_COPY
      ? { kind: 'domain', code: code as ToolUsageDomainErrorCode }
      : { kind: 'door', code: code as ToolUsageReusableErrorCode };
  });

  /** The `days` value the reads carry — the window mapped onto UTC days. */
  readonly windowDays = computed<number>(
    () => ToolUsageTabComponent.WINDOW_DAYS[this.windowChoice()] ?? 7,
  );

  constructor() {
    effect(() => {
      // The project + window are the triggers — either re-reads the summary.
      const projectId = this.projectId();
      const days = this.windowDays();
      untracked(() => this.fetchSummary());
    });
    effect(() => {
      // The selection is the trigger — a new tool re-reads its daily series.
      const tool = this.selectedTool();
      untracked(() => {
        if (tool === null) {
          this.daily.set(null);
          this.dailyError.set(null);
          return;
        }
        this.fetchDaily(tool);
      });
    });
  }

  ngOnInit(): void {
    this.projectService.listProjects().subscribe({
      next: (payload) => {
        this.projects.set(payload.projects);
      },
      error: (err: unknown) => {
        console.error('[tool-usage] project list failed:', err);
        // No dedicated project-error signal exists (the component's
        // existing error path is the summary banner); surface the failure
        // there rather than inventing a new state shape.
        this.summaryError.set(decodeToolUsageError(err));
      },
    });
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    this.pollHandle = setInterval(() => this.tick(), ToolUsageTabComponent.POLL_INTERVAL_MS);
  }

  ngOnDestroy(): void {
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  /** The poll tick — the browser-visibility gate + the skip-if-in-flight guard. */
  private tick(): void {
    if (!this.browserVisible || this.summaryInFlight) {
      return;
    }
    this.fetchSummary();
  }

  /** The visibility gate — a hidden browser tab stops the poll. */
  private readonly onVisibilityChange = (): void => {
    this.browserVisible = document.visibilityState !== 'hidden';
  };

  /**
   * The select's seed — the frame's window when this tab's own ladder
   * can serve it, else the measurement window (`7d`).
   */
  private seedWindow(): ResourcesWindow {
    const frameWindow = this.window();
    const ladder = this.ladder;
    return ladder.includes(frameWindow as ResourcesWindow)
      ? (frameWindow as ResourcesWindow)
      : '7d';
  }

  /** The project select — `''` means All projects (the param is omitted). */
  onProjectChange(value: string): void {
    this.projectId.set(value);
  }

  /** The window select — the tab-local ladder only. */
  onWindowChange(value: string): void {
    if ((this.ladder as readonly string[]).includes(value)) {
      this.windowChoice.set(value as ResourcesWindow);
    }
  }

  /** The summary read — its OWN supersede class. */
  fetchSummary(): void {
    const token = ++this.summarySeq;
    this.summaryInFlight = true;
    this.toolUsage
      .summary({
        projectId: this.projectId() === '' ? undefined : this.projectId(),
        days: this.windowDays(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          this.summaryInFlight = false;
          if (token !== this.summarySeq) {
            return; // superseded — a stale answer never lands
          }
          this.rows.set(payload.rows);
          this.summaryError.set(null);
          this.generatedAt.set(payload.generated_at);
        },
        error: (err: unknown) => {
          this.summaryInFlight = false;
          if (token !== this.summarySeq) {
            return;
          }
          // The last-good rows are RETAINED under the error banner.
          this.summaryError.set(decodeToolUsageError(err));
        },
      });
  }

  /** The operator-initiated summary retry — always fires. */
  readonly retrySummary = (): void => {
    this.fetchSummary();
  };

  /** The daily read — its OWN supersede class. */
  fetchDaily(toolName: string): void {
    const token = ++this.dailySeq;
    this.dailyError.set(null);
    this.daily.set(null);
    this.toolUsage
      .daily({
        projectId: this.projectId() === '' ? undefined : this.projectId(),
        toolName,
        days: this.windowDays(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          if (token !== this.dailySeq) {
            return;
          }
          this.daily.set(payload.series);
        },
        error: (err: unknown) => {
          if (token !== this.dailySeq) {
            return;
          }
          this.daily.set(null);
          this.dailyError.set(decodeToolUsageError(err));
        },
      });
  }

  /** The operator-initiated daily retry — re-reads the selected tool. */
  readonly retryDaily = (): void => {
    const tool = this.selectedTool();
    if (tool !== null) {
      this.fetchDaily(tool);
    }
  };

  /**
   * The row click — select the tool (breakdown expansion + trend fetch,
   * both from data ALREADY fetched or the one daily read). Clicking the
   * selected row again collapses it (ONE expansion at a time).
   */
  toggleRow(toolName: string): void {
    if (this.selectedTool() === toolName) {
      this.selectedTool.set(null);
      return;
    }
    this.selectedTool.set(toolName);
  }

  /** A1 — the zero/near-zero test (calls_7d ≤ 3, a LOCAL affordance). */
  isNearZero(row: ToolUsageRow): boolean {
    return row.calls_7d <= ToolUsageTabComponent.NEAR_ZERO_CALLS_7D;
  }

  /** The domain-local copy of a domain-only outcome. */
  domainCopy(code: ToolUsageDomainErrorCode) {
    return TOOL_USAGE_DOMAIN_COPY[code];
  }

  /**
   * The chart series — ONE series for the SELECTED tool (A3): the
   * per-UTC-day points mapped to epoch ms (explicit `T00:00:00Z` — the
   * house UTC-never-local rule), counters rendered STEP (the house
   * COUNTER-HONESTY: step-never-diagonal), palette from the SSOT.
   */
  readonly trendSeries = computed<readonly ChartSeries[]>(() => {
    const tool = this.selectedTool();
    const points = this.daily();
    if (tool === null || points === null) {
      return [];
    }
    return [
      {
        id: tool,
        label: tool,
        color: chartSeriesColor(0),
        mode: 'step' as const,
        points: points
          .map((point) => ({ t: utcDayMs(point.day), v: point.calls }))
          .filter((point) => Number.isFinite(point.t)),
      },
    ];
  });
}

/**
 * The UTC-day → epoch-ms mapping — `Date.parse` over an EXPLICIT UTC
 * midnight (the house UTC-never-local rule). `NaN` is filtered by the
 * caller (a malformed day string renders nothing, never a wrong axis).
 */
function utcDayMs(day: string): number {
  return Date.parse(`${day}T00:00:00Z`);
}
