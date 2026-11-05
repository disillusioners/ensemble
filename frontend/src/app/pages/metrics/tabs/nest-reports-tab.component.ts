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

import { MetricsCentralTabComponent } from './central-tab.component';
import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from '../metrics-state';
import {
  MetricsRangeOption,
  metricsCentralWindowSeconds,
} from '../metrics-range';
import { metricsCentralTab } from '../tabs.manifest';
import { MetricsDoorStateComponent } from '../metrics-door-state.component';
import { NestReportsService } from '../../../services/nest-reports.service';
import {
  AnomalyClusterDetailPayload,
  AnomalyClusterGroup,
  NestReportsBanner,
  NestReportsDomainErrorCode,
  NestReportsErrorCode,
  NestReportsReusableErrorCode,
  NEST_REPORTS_DOMAIN_COPY,
  decodeNestReportsError,
} from '../../../models/nest-reports.models';

/**
 * The NEST REPORTS tab body (the nest-anomaly lane, phase 4).
 *
 * ── THE SHAPE (R2 — a CENTRAL tab with a CUSTOM body) ───────────────────
 *
 * The tab is a `central` manifest entry (its METRIC half — F23 — comes
 * through the central door) whose body is a custom component. It is
 * the runtime-tab wrapper PLUS a second section:
 *
 *   | section | source      | transport                                   |
 *   |---------|-------------|---------------------------------------------|
 *   | A       | central door| the SHARED `app-metrics-central-tab`         |
 *   | B       | domain route| the NEW `NestReportsService`                  |
 *
 * Section A is the SHARED body (it owns the fetch, the panel
 * composition, the stat strip and the last-good retention) — the charts
 * ride it, so this file imports NO chart library (the bundle guard's
 * B2/B3/B4a legs stay at zero exposure).
 *
 * ── WHY SECTION B's STATE IS LOCAL (AM-1) ────────────────────────────────
 *
 * The frame owns ONE state slot per tab, and that slot is shaped for
 * the CENTRAL door (one `errorClass`, one `lastGood`, one `series`).
 * Two sources cannot share it ⇒ the domain half's state lives in THIS
 * body's own signals (the resource tab's own-source pattern). No
 * clobbering, no frame change, no state-model change.
 *
 * ── THE SINGLE-BANNER DISCIPLINE ─────────────────────────────────────────
 *
 * The central half's failure is rendered by the shared body; the
 * domain half's failure is rendered by the domain-local panel below.
 * The domain panel is SUPPRESSED when the central half is also failing
 * (`state().errorClass !== null`) — both sources share ONE posture, and
 * a posture failure that affects both is explained ONCE.
 *
 * The AUTH-POSTURE classes (`auth_not_configured`, `scope_insufficient`,
 * `auth_required`) are ALSO suppressed: the metrics FRAME renders that
 * posture page-level (its own catalog banner — the shipped behaviour
 * for every central tab), so a second banner in this tab would explain
 * the same daemon configuration twice. The section still renders a
 * degraded placeholder (the tab never blanks) — it just carries no
 * banner of its own.
 *
 * ── THE GROUPING IS SERVER-SIDE (AM-6c) ─────────────────────────────────
 *
 * The route returns GROUPS (one per tree root × canonical category).
 * This body renders them VERBATIM — it never re-keys, re-windows or
 * merges them, and it never recomputes the server's `open` boolean or
 * its `severity_rank`. The occurrence badge is the group's
 * `occurrence_count` (the number of CLUSTERS, not reports).
 *
 * ── THE EXCERPT IS OPT-IN AND CAPPED (AM-12b / AM-12c) ──────────────────
 *
 * The excerpt arrives ONLY behind `include_excerpt=true` (an operator
 * toggle). When it arrives it renders at most 300 characters with a
 * reveal affordance — never raw.
 *
 * ── THE TEMPLATE IS SAFE (AM-12d) ───────────────────────────────────────
 *
 * The template contains NO `innerHTML` and NO `bypassSecurityTrust` —
 * the excerpt is rendered as TEXT (the tab pack pins the property).
 */
@Component({
  selector: 'app-metrics-nest-reports-tab',
  standalone: true,
  imports: [MatButtonModule, MetricsCentralTabComponent, MetricsDoorStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-metrics-central-tab
      [entry]="entry"
      [state]="state()"
      [onState]="onState()"
      [window]="window()"
      [baseline]="baseline()"
      [onRangeChange]="onRangeChange()"
    ></app-metrics-central-tab>

    <section class="nest-reports" data-test="nest-reports-section">
      <h3 class="nest-reports-title">
        Incident list <span class="tab-hint">the durable anomaly store — grouped by tree root × category</span>
      </h3>

      @if (suppressed(); as note) {
        <!--
          THE DOMAIN HALF IS SUPPRESSED (the single-banner discipline).

          The posture is explained ONCE — by the central half (or by the
          frame's page-level banner for the auth classes). The section
          still renders, so the tab never blanks.
        -->
        <p class="tab-hint" data-test="nest-reports-suppressed">{{ note }}</p>
      } @else {
        @if (domainBanner(); as banner) {
          @if (banner.kind === 'domain') {
            <div
              class="nest-reports-error"
              data-test="nest-reports-error"
              [attr.data-error-class]="banner.code"
            >
              <span data-test="nest-reports-error-cause">{{
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
                  (click)="retryList()"
                  data-test="nest-reports-retry"
                >
                  Retry
                </button>
              }
            </div>
          } @else {
            <app-metrics-door-state
              [code]="banner.code"
              [onRetry]="retryList"
              testId="nest-reports-error"
              retryTestId="nest-reports-retry"
            ></app-metrics-door-state>
          }
        }

        @if (groups(); as rows) {
          @if (rows.length === 0) {
            <p class="tab-hint" data-test="nest-reports-empty">
              No clusters in the window — the nest reported nothing in this window.
            </p>
          } @else {
            <table class="nest-reports-table" data-test="nest-reports-incident-table">
              <thead>
                <tr>
                  <th scope="col">Severity</th>
                  <th scope="col">Category</th>
                  <th scope="col">Occurrences</th>
                  <th scope="col">Reports</th>
                  <th scope="col">Tree root</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last report</th>
                  <th scope="col">Board</th>
                </tr>
              </thead>
              <tbody>
                @for (group of rows; track rowKey(group)) {
                  <tr
                    class="nest-reports-row"
                    [class.nest-reports-row-open]="isExpanded(group)"
                    [attr.data-test]="'nest-reports-row-' + rowKey(group)"
                    (click)="toggleGroup(group)"
                  >
                    <td>
                      <span
                        [class]="'chip chip-' + severityTier(group.severity_rank)"
                        [attr.data-rank]="group.severity_rank"
                        data-test="nest-reports-severity-chip"
                      >
                        {{ group.severity_label }}
                      </span>
                    </td>
                    <td>{{ group.category }}</td>
                    <td>
                      <span class="badge" data-test="nest-reports-occurrence-badge">
                        {{ group.occurrence_count }}
                      </span>
                    </td>
                    <td>{{ group.report_count }}</td>
                    <td>{{ group.tree_root_instance_id }}</td>
                    <td>
                      <span
                        class="badge"
                        [attr.data-open]="group.open"
                        data-test="nest-reports-status-badge"
                      >
                        {{ group.open ? 'open' : 'closed' }}
                      </span>
                    </td>
                    <td data-test="nest-reports-last-report">
                      {{ relativeAge(group.last_report_at) }} · {{ group.last_report_at }}
                    </td>
                    <td>
                      @if (group.board_item_ids?.length) {
                        <span class="badge" data-test="nest-reports-board">
                          board {{ group.board_item_ids?.join(', ') }}
                        </span>
                      }
                    </td>
                  </tr>
                  @if (isExpanded(group)) {
                    <tr data-test="nest-reports-members">
                      <td colspan="8">
                        <div class="nest-reports-members">
                          <span class="tab-hint">
                            {{ group.occurrence_count }} cluster(s) — click one for its members
                          </span>
                          <div class="member-list">
                            @for (clusterId of group.cluster_ids; track clusterId) {
                              <button
                                mat-stroked-button
                                type="button"
                                class="member-button"
                                (click)="openCluster(clusterId, $event)"
                                [attr.data-test]="'nest-reports-member-' + clusterId"
                              >
                                {{ clusterId }}
                              </button>
                            }
                          </div>
                          @if (detailError(); as code) {
                            <p
                              class="tab-hint"
                              data-test="nest-reports-detail-error"
                              [attr.data-error-class]="code"
                            >
                              {{ domainCopy(code).cause }}
                            </p>
                          }
                          @if (detail(); as cluster) {
                            <div class="member-reports" data-test="nest-reports-member-reports">
                              <p class="tab-hint">
                                {{ cluster.report_count }} report(s) · {{ cluster.severity_label }}
                              </p>
                              <button
                                mat-stroked-button
                                type="button"
                                (click)="toggleExcerpt(); $event.stopPropagation()"
                                data-test="nest-reports-excerpt-toggle"
                              >
                                {{ includeExcerpt() ? 'Hide excerpts' : 'Show excerpts' }}
                              </button>
                              @for (report of cluster.reports; track report.report_id) {
                                <div class="member-report" [attr.data-test]="'nest-reports-report-' + report.report_id">
                                  <span class="tab-hint">
                                    {{ report.created_at }} · {{ report.agent_id }} ·
                                    {{ report.instance_id }} · {{ report.severity }}
                                  </span>
                                  <span class="member-description">{{ report.description }}</span>
                                  @if (report.excerpt !== undefined) {
                                    <span class="member-excerpt" data-test="nest-reports-excerpt">
                                      {{ excerptText(report.excerpt, report.report_id) }}
                                    </span>
                                    @if (report.excerpt.length > excerptCap) {
                                      <button
                                        mat-stroked-button
                                        type="button"
                                        (click)="toggleReveal(report.report_id, $event)"
                                        [attr.data-test]="'nest-reports-excerpt-reveal-' + report.report_id"
                                      >
                                        {{ isRevealed(report.report_id) ? 'Hide the rest' : 'Show the rest' }}
                                      </button>
                                    }
                                  }
                                </div>
                              }
                            </div>
                          }
                        </div>
                      </td>
                    </tr>
                  }
                }
              </tbody>
            </table>
          }
        }

        @if (freshnessLine(); as line) {
          <p class="tab-hint" data-test="nest-reports-freshness">{{ line }}</p>
        }
        @if (boundNote(); as note) {
          <p class="tab-hint" data-test="nest-reports-bound">{{ note }}</p>
        }
      }
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

      .nest-reports-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.8rem;
      }

      .nest-reports-row {
        cursor: pointer;
      }

      .nest-reports-row-open {
        background: #111c2e;
      }

      .chip,
      .badge {
        display: inline-block;
        border-radius: 6px;
        border: 1px solid #334155;
        padding: 0 0.35rem;
        font-size: 0.72rem;
      }

      .chip-sev-1 {
        color: #fde047;
      }

      .chip-sev-2 {
        color: #fdba74;
      }

      .chip-sev-3,
      .badge[data-open='true'] {
        color: #fca5a5;
      }

      .badge[data-open='false'] {
        color: #94a3b8;
      }

      .nest-reports-error {
        border: 1px solid #7f1d1d;
        border-radius: 6px;
        color: #fca5a5;
        padding: 0.5rem 0.75rem;
        margin-bottom: 0.6rem;
        font-size: 0.8rem;
      }

      .member-excerpt {
        display: block;
        font-family: monospace;
        font-size: 0.72rem;
        color: #94a3b8;
        overflow-wrap: anywhere;
      }
    `,
  ],
})
export class NestReportsTabComponent implements OnInit, OnDestroy {
  /** The manifest entry — the F23 binding. */
  readonly entry = metricsCentralTab('nest-reports');

  /** This tab's state — written by the CENTRAL source (the shared body). */
  readonly state = input<MetricsTabState>(EMPTY_METRICS_TAB_STATE);

  /** The frame's per-tab state reporter. */
  readonly onState = input<MetricsTabStateReporter | null>(null);

  /** The frame's window VALUE (the union). */
  readonly window = input<MetricsRangeOption>('1h');

  /** Whether the previous-window comparison is ON (the central tabs only). */
  readonly baseline = input(false);

  /** The frame's range-change reporter. */
  readonly onRangeChange = input<(option: MetricsRangeOption) => void>(() => undefined);

  /** The incident list's poll cadence — the house 15 s budget. */
  static readonly POLL_INTERVAL_MS = 15_000;

  /** The excerpt cap — the member list renders at most this many chars. */
  static readonly EXCERPT_CAP = 300;

  /** The cap as a template field (the reveal affordance's threshold). */
  readonly excerptCap = NestReportsTabComponent.EXCERPT_CAP;

  private readonly nestReports = inject(NestReportsService);
  private readonly destroyRef = inject(DestroyRef);

  // ── SECTION B's OWN STATE (the domain source's state, local) ──────────

  /** The groups — `null` until the first answer. */
  readonly groups = signal<readonly AnomalyClusterGroup[] | null>(null);
  /** The list's failure class (decoded). */
  readonly listError = signal<NestReportsErrorCode | null>(null);
  /** The SERVER's freshness line (its own sentence — never the client clock). */
  readonly freshnessLine = signal<string | null>(null);
  /** The SERVER's query instant — the relative-age reference (never the client clock). */
  private readonly freshnessQueriedAt = signal<string | null>(null);
  /** The server's own bound note (`narrow_hint`) — never silent. */
  readonly boundNote = signal<string | null>(null);
  /** The expanded group's key (`tree root::category`) — ONE row at a time. */
  readonly expandedKey = signal<string | null>(null);
  /** The opened cluster's members (the detail read). */
  readonly detail = signal<AnomalyClusterDetailPayload | null>(null);
  /** The detail read's failure class (the 404 is a DOMAIN outcome). */
  readonly detailError = signal<NestReportsErrorCode | null>(null);
  /** Whether the operator asked for the excerpt (AM-12b). */
  readonly includeExcerpt = signal(false);
  /** The revealed excerpts (per report id) — the reveal affordance (AM-12c). */
  private readonly revealed = signal<ReadonlySet<string>>(new Set<string>());

  /** The supersede tokens — ONE per request CLASS (the list, the detail). */
  private listSeq = 0;
  private detailSeq = 0;
  private listInFlight = false;
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  /** Whether the browser tab is visible — the poll gate. */
  private browserVisible = true;

  /** The window the list is asked for — the frame's window, in seconds. */
  readonly windowS = computed(() => {
    const seconds = metricsCentralWindowSeconds(this.window());
    return seconds ?? 3600;
  });

  /** The domain error classes the FRAME states page-level (never twice). */
  private static readonly FRAME_STATED_CLASSES: readonly NestReportsErrorCode[] = [
    'auth_not_configured',
    'scope_insufficient',
    'auth_required',
  ];

  /** Whether the domain panel is suppressed — the single-banner rule. */
  readonly suppressed = computed(() => {
    // The central half's OWN failure explains the posture once.
    if (this.state().errorClass !== null) {
      return 'the incident list is unavailable — the central door above carries the failure.';
    }
    const code = this.listError();
    if (code && NestReportsTabComponent.FRAME_STATED_CLASSES.includes(code)) {
      return "the incident list is unavailable under this daemon's auth posture.";
    }
    return null;
  });

  /**
   * The banner the domain panel renders.
   *
   * The DISCRIMINATED shape (not a bare code) is load-bearing: the
   * shared `app-metrics-door-state` accepts a DOOR class only, and the
   * domain-only classes are not door classes. The shape lets the
   * template branch on the KIND and hand the shared panel a code it
   * can actually render.
   */
  readonly domainBanner = computed<NestReportsBanner | null>(() => {
    const code = this.listError();
    if (!code || this.suppressed()) {
      return null;
    }
    return code in NEST_REPORTS_DOMAIN_COPY
      ? { kind: 'domain', code: code as NestReportsDomainErrorCode }
      : { kind: 'door', code: code as NestReportsReusableErrorCode };
  });

  constructor() {
    effect(() => {
      // The window is the trigger — a new window re-reads the list.
      const seconds = this.windowS();
      untracked(() => this.fetchList(seconds));
    });
  }

  ngOnInit(): void {
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    this.pollHandle = setInterval(() => this.tick(), NestReportsTabComponent.POLL_INTERVAL_MS);
  }

  ngOnDestroy(): void {
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  /** The poll tick — the browser-visibility gate + the skip-if-in-flight. */
  private tick(): void {
    if (!this.browserVisible || this.listInFlight) {
      return;
    }
    this.fetchList(this.windowS());
  }

  /** The visibility gate — a hidden browser tab stops the poll. */
  private readonly onVisibilityChange = (): void => {
    this.browserVisible = document.visibilityState !== 'hidden';
  };

  /** The list read — its OWN supersede class. */
  fetchList(windowS: number): void {
    const token = ++this.listSeq;
    this.listInFlight = true;
    this.nestReports
      .getClusters({ windowS })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          this.listInFlight = false;
          if (token !== this.listSeq) {
            return; // superseded — a stale answer never lands
          }
          this.groups.set(payload.groups);
          this.listError.set(null);
          this.freshnessLine.set(payload.freshness?.line ?? null);
          this.freshnessQueriedAt.set(payload.freshness?.queried_at_utc ?? null);
          this.boundNote.set(payload.bound?.narrow_hint ?? null);
        },
        error: (err: unknown) => {
          this.listInFlight = false;
          if (token !== this.listSeq) {
            return;
          }
          // The last-good groups are RETAINED under the error banner.
          this.listError.set(decodeNestReportsError(err));
        },
      });
  }

  /** The operator-initiated retry — always fires. */
  retryList(): void {
    this.fetchList(this.windowS());
  }

  /** The group's stable key — the expansion state keys on it. */
  groupKey(group: AnomalyClusterGroup): string {
    return `${group.tree_root_instance_id}::${group.category}`;
  }

  /** The row hook's key — the group's FRESHEST member cluster id. */
  rowKey(group: AnomalyClusterGroup): string {
    return group.cluster_ids[0] ?? this.groupKey(group);
  }

  /** Whether the group is the expanded one (ONE row at a time). */
  isExpanded(group: AnomalyClusterGroup): boolean {
    return this.expandedKey() === this.groupKey(group);
  }

  /** The row click — expand the group's member clusters (NO extra call). */
  toggleGroup(group: AnomalyClusterGroup): void {
    const key = this.groupKey(group);
    if (this.expandedKey() === key) {
      this.expandedKey.set(null);
      this.detail.set(null);
      this.detailError.set(null);
      return;
    }
    this.expandedKey.set(key);
    this.detail.set(null);
    this.detailError.set(null);
  }

  /** A member cluster id click — the DETAIL read (its own supersede class). */
  openCluster(clusterId: string, event?: Event): void {
    event?.stopPropagation();
    this.fetchDetail(clusterId);
  }

  /** The detail read — its OWN supersede class. */
  fetchDetail(clusterId: string): void {
    const token = ++this.detailSeq;
    this.detailError.set(null);
    this.nestReports
      .getClusterDetail(clusterId, { includeExcerpt: this.includeExcerpt() })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          if (token !== this.detailSeq) {
            return;
          }
          this.detail.set(payload);
        },
        error: (err: unknown) => {
          if (token !== this.detailSeq) {
            return;
          }
          this.detail.set(null);
          this.detailError.set(decodeNestReportsError(err));
        },
      });
  }

  /** The operator's excerpt toggle — it re-reads the opened cluster. */
  toggleExcerpt(): void {
    this.includeExcerpt.update((current) => !current);
    const detail = this.detail();
    if (detail) {
      this.fetchDetail(detail.cluster_id);
    }
  }

  /** The domain-local copy of a domain-only outcome. */
  domainCopy(code: NestReportsErrorCode) {
    return NEST_REPORTS_DOMAIN_COPY[code as keyof typeof NEST_REPORTS_DOMAIN_COPY];
  }

  /** The severity tier — the ROUTE's rank integer, never a name. */
  severityTier(rank: number): string {
    return rank === 3 || rank === 2 || rank === 1 ? `sev-${rank}` : 'sev-0';
  }


  /** The excerpt cap — at most 300 characters are rendered. */
  excerptText(excerpt: string, reportId: string): string {
    if (this.isRevealed(reportId)) {
      return excerpt;
    }
    return excerpt.slice(0, NestReportsTabComponent.EXCERPT_CAP);
  }

  /** Whether the excerpt is revealed. */
  isRevealed(reportId: string): boolean {
    return this.revealed().has(reportId);
  }

  /** The reveal affordance — never the raw excerpt by default. */
  toggleReveal(reportId: string, event?: Event): void {
    event?.stopPropagation();
    this.revealed.update((current) => {
      const next = new Set(current);
      if (next.has(reportId)) {
        next.delete(reportId);
      } else {
        next.add(reportId);
      }
      return next;
    });
  }

  /**
   * The RELATIVE age of the last report — measured against the SERVER's
   * own query instant (the freshness block), never the client clock (the
   * house rule).
   */
  relativeAge(at: string): string {
    const queriedAt = this.freshnessQueriedAt();
    const reference = queriedAt ? Date.parse(queriedAt) : Number.NaN;
    if (!Number.isFinite(reference)) {
      return at;
    }
    const seconds = Math.max(0, Math.round((reference - Date.parse(at)) / 1000));
    if (seconds < 60) {
      return `${seconds}s ago`;
    }
    if (seconds < 3600) {
      return `${Math.round(seconds / 60)}m ago`;
    }
    return `${Math.round(seconds / 3600)}h ago`;
  }

}
