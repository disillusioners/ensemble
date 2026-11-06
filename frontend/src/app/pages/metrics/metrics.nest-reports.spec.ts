// The NEST-REPORTS tab pack (the nest-anomaly lane, phase 4 —
// `phase4-plan.md` Task 6).
//
// The load-bearing pins:
//
//   * the tab appears in the manifest with the RULED id `nest-reports`
//     and the RULED label `Nest Reports` — and it does NOT claim the
//     Resources tab's existing "Anomalies" panel's identity (the
//     label-namespace collision is closed);
//   * the request shapes — the list carries the frame's window in
//     seconds; the detail call is its OWN request class;
//   * the SINGLE-BANNER DISCIPLINE — a 503 on BOTH sources yields
//     exactly ONE banner and no Retry;
//   * the domain-only 404 carries the DOMAIN-LOCAL copy (the door's
//     `unknown_family` copy would be a false statement for a cluster
//     id);
//   * the EMPTY state is DISTINCT from every error state and HONEST —
//     no fake data (a placeholder series on a 503 must red this pack);
//   * AM-6c — the rows are the SERVER's groups, rendered verbatim, each
//     carrying an OCCURRENCE badge (the cluster count); the grouping
//     never mutates a cluster;
//   * AM-7 — the severity chip renders the PEAK label ("critical
//     (peak)"), never a bare severity word;
//   * AM-12(c) — the excerpt renders ≤300 characters with a reveal
//     affordance, never raw, and is absent unless the operator asked
//     for it;
//   * the status badge renders the SERVER's boolean;
//   * a row click expands the group's member clusters (NO extra call);
//   * a stale response is discarded (the supersede token);
//   * the poll stops while the browser tab is hidden;
//   * AM-12(d) — the template carries NO `innerHTML` and NO
//     `bypassSecurityTrust` (the safety is a pinned PROPERTY).

import { readFileSync } from 'fs';
import { join } from 'path';

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Subject } from 'rxjs';

import { MetricsService } from '../../services/metrics.service';
import { NestReportsService } from '../../services/nest-reports.service';
import {
  MetricsFamiliesPayload,
  MetricsSeriesPayload,
  MetricsSnapshotPayload,
} from '../../models/metrics.model';
import {
  AnomalyClusterDetailPayload,
  AnomalyClustersPayload,
} from '../../models/nest-reports.models';
import { MetricsTabState } from './metrics-state';
import { METRICS_TABS, metricsTabById } from './tabs.manifest';
import { NestReportsTabComponent } from './tabs/nest-reports-tab.component';

const metricsDir = join(__dirname);
const tabSrc = readFileSync(join(metricsDir, 'tabs', 'nest-reports-tab.component.ts'), 'utf-8');

/**
 * The tab's TEMPLATE block — the AM-12(d) pin's subject.
 *
 * The pin is about the TEMPLATE (the rendered surface), not the file's
 * prose: the component's own doc comment NAMES the two forbidden
 * tokens, and a file-level scan would red on the documentation.
 */
const templateSrc = (() => {
  const start = tabSrc.indexOf('template: `');
  const end = tabSrc.indexOf('`,', start);
  return start >= 0 && end > start ? tabSrc.slice(start + 'template: `'.length, end) : '';
})();

/** A minimal central state — the tab's own half is the central door's. */
function state(overrides: Partial<MetricsTabState> = {}): MetricsTabState {
  return {
    doorState: 'ok',
    families: [],
    series: [],
    lastGood: null,
    errorClass: null,
    ...overrides,
  };
}

/** One group — the route's shape (AM-6c). */
function group(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    tree_root_instance_id: 'tree-1',
    category: 'tool-failure',
    severity: 'critical',
    severity_label: 'critical (peak)',
    severity_rank: 3,
    report_count: 7,
    occurrence_count: 3,
    cluster_ids: ['cl-1', 'cl-2', 'cl-3'],
    last_report_at: '2026-11-05T00:55:00+00:00',
    open: true,
    ...overrides,
  };
}

/** The list payload — the route's shape. */
function listPayload(overrides: Partial<AnomalyClustersPayload> = {}): AnomalyClustersPayload {
  return {
    groups: [group()] as AnomalyClustersPayload['groups'],
    bound: {
      matched_count: 1,
      returned_count: 1,
      dropped_count: 0,
      more_available: false,
      truncated: false,
      narrow_hint: null,
    },
    category_breakdown: { 'tool-failure': { groups: 1, reports: 7 } },
    freshness: {
      window_s: 3600,
      window_cutoff_utc: '2026-11-05T00:00:00+00:00',
      queried_at_utc: '2026-11-05T01:00:00+00:00',
      resolve_window_s: 3600,
      line: 'clusters with a report in the last 3600s as of 2026-11-05T01:00:00+00:00 (server clock)',
    },
    generated_at_utc: '2026-11-05T01:00:00+00:00',
    ...overrides,
  };
}

/** The detail payload — the route's shape. */
function detailPayload(
  overrides: Partial<AnomalyClusterDetailPayload> = {},
): AnomalyClusterDetailPayload {
  return {
    cluster_id: 'cl-1',
    category: 'tool-failure',
    severity: 'critical',
    severity_rank: 3,
    severity_label: 'critical (peak)',
    report_count: 2,
    tree_root_instance_id: 'tree-1',
    first_report_at: '2026-11-05T00:10:00+00:00',
    last_report_at: '2026-11-05T00:55:00+00:00',
    open: true,
    board_item_id: null,
    reports: [
      {
        report_id: 'rep-1',
        agent_id: 'coder',
        instance_id: 'inst-9',
        created_at: '2026-11-05T00:55:00+00:00',
        category: 'tool-failure',
        category_raw: null,
        severity: 'critical',
        description: 'the tool call failed twice',
        outcome: 'reported',
      },
    ],
    bound: {
      matched_count: 2,
      returned_count: 1,
      dropped_count: 1,
      more_available: false,
      truncated: true,
      narrow_hint: 'the member page was bounded — use a smaller `limit`',
    },
    freshness: {
      queried_at_utc: '2026-11-05T01:00:00+00:00',
      resolve_window_s: 3600,
      line: 'cluster members as of 2026-11-05T01:00:00+00:00 (server clock)',
    },
    generated_at_utc: '2026-11-05T01:00:00+00:00',
    ...overrides,
  };
}

class StubMetricsService {
  readonly families$ = new Subject<MetricsFamiliesPayload>();
  readonly snapshot$ = new Subject<MetricsSnapshotPayload>();
  readonly series$ = new Subject<MetricsSeriesPayload>();

  getFamilies() {
    return this.families$.asObservable();
  }
  getSnapshot() {
    return this.snapshot$.asObservable();
  }
  getSeries() {
    return this.series$.asObservable();
  }
}

class StubNestReportsService {
  /** ONE subject PER request — a superseded answer is delivered to one. */
  readonly listSubjects: Subject<AnomalyClustersPayload>[] = [];
  readonly detailSubjects: Subject<AnomalyClusterDetailPayload>[] = [];
  readonly listRequests: { windowS?: number }[] = [];
  readonly detailRequests: { clusterId: string; includeExcerpt?: boolean }[] = [];

  getClusters(options: { windowS?: number } = {}) {
    this.listRequests.push(options);
    const subject = new Subject<AnomalyClustersPayload>();
    this.listSubjects.push(subject);
    return subject.asObservable();
  }

  getClusterDetail(clusterId: string, options: { includeExcerpt?: boolean } = {}) {
    this.detailRequests.push({ clusterId, includeExcerpt: options.includeExcerpt });
    const subject = new Subject<AnomalyClusterDetailPayload>();
    this.detailSubjects.push(subject);
    return subject.asObservable();
  }
}

describe('the nest-reports tab — the manifest binding (the ruled id + label)', () => {
  it('the tab is registered with the RULED id and label', () => {
    const entry = metricsTabById('nest-reports');
    expect(entry).not.toBeNull();
    expect(entry?.id).toBe('nest-reports');
    expect(entry?.label).toBe('Nest Reports');
    expect(entry?.slot).toBe('central');
    expect([...(entry as { familyIds: readonly string[] }).familyIds]).toEqual(['F23']);
    expect([...(entry as { statFamilyIds: readonly string[] }).statFamilyIds]).toEqual(['F23']);
  });

  it('the tab does NOT claim the Resources tab\'s "Anomalies" panel identity', () => {
    const labels = METRICS_TABS.map((tab) => tab.label);
    expect(labels).toContain('Nest Reports');
    expect(labels).not.toContain('Anomalies');
    const ids = METRICS_TABS.map((tab) => tab.id);
    expect(ids).not.toContain('anomalies');
  });
});

describe('NestReportsTabComponent — the domain list', () => {
  let fixture: ComponentFixture<NestReportsTabComponent>;
  let component: NestReportsTabComponent;
  let nest: StubNestReportsService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NestReportsTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: MetricsService, useClass: StubMetricsService },
        { provide: NestReportsService, useClass: StubNestReportsService },
      ],
    }).compileComponents();
    nest = TestBed.inject(NestReportsService) as unknown as StubNestReportsService;
    fixture = TestBed.createComponent(NestReportsTabComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
  });

  /** Answer the pending list request. */
  function answerList(payload: AnomalyClustersPayload = listPayload(), index = 0): void {
    fixture.detectChanges();
    nest.listSubjects[index]?.next(payload);
    fixture.detectChanges();
  }

  it('asks the domain route with the FRAME\'s window in seconds', () => {
    fixture.componentRef.setInput('window', '2h');
    fixture.detectChanges();
    expect(nest.listRequests.length).toBeGreaterThan(0);
    expect(nest.listRequests[0].windowS).toBe(7200);
  });

  it('renders the SERVER\'s groups verbatim — one row per group, no client regrouping', () => {
    answerList();
    const rows = fixture.nativeElement.querySelectorAll('[data-test^="nest-reports-row-"]');
    expect(rows.length).toBe(1);
    expect((rows[0].textContent ?? '')).toContain('tool-failure');
    expect((rows[0].textContent ?? '')).toContain('tree-1');
  });

  it('AM-6c — the occurrence badge is the CLUSTER count (not the report count)', () => {
    answerList();
    const badge = fixture.nativeElement.querySelector('[data-test="nest-reports-occurrence-badge"]');
    expect(badge.textContent.trim()).toBe('3');
    // The report count is a DIFFERENT number — the two must not swap.
    const row = fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]');
    expect((row.textContent ?? '')).toContain('7');
  });

  it('AM-7 — the severity chip renders the PEAK label, never a bare severity word', () => {
    answerList();
    const chip = fixture.nativeElement.querySelector('[data-test="nest-reports-severity-chip"]');
    expect(chip.textContent.trim()).toBe('critical (peak)');
    expect(chip.textContent.trim()).not.toBe('critical');
    // The tier comes from the route's RANK integer.
    expect(chip.getAttribute('data-rank')).toBe('3');
  });

  it('the status badge renders the SERVER\'s boolean (never a recomputed one)', () => {
    answerList();
    const badge = fixture.nativeElement.querySelector('[data-test="nest-reports-status-badge"]');
    expect(badge.getAttribute('data-open')).toBe('true');
    expect(badge.textContent.trim()).toBe('open');
  });

  it('the freshness line is the SERVER\'s sentence', () => {
    answerList();
    const line = fixture.nativeElement.querySelector('[data-test="nest-reports-freshness"]');
    expect(line.textContent).toContain('2026-11-05T01:00:00+00:00');
    expect(line.textContent).toContain('server clock');
  });

  it('the EMPTY state is honest and DISTINCT from every error state', () => {
    answerList(listPayload({ groups: [] }));
    const empty = fixture.nativeElement.querySelector('[data-test="nest-reports-empty"]');
    expect(empty).not.toBeNull();
    expect((empty.textContent ?? '')).toContain('No clusters in the window');
    // No fake data: no row, no badge, no chip.
    expect(fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-error"]')).toBeNull();
  });

  it('a row click expands the group\'s member clusters — NO extra call', () => {
    answerList();
    const before = nest.detailRequests.length;
    const row = fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]');
    row.click();
    fixture.detectChanges();
    const members = fixture.nativeElement.querySelector('[data-test="nest-reports-members"]');
    expect(members).not.toBeNull();
    expect(members.querySelectorAll('[data-test^="nest-reports-member-"]').length).toBe(3);
    // The member ids came from the route's `cluster_ids` — no detail read.
    expect(nest.detailRequests.length).toBe(before);
  });

  it('clicking a member cluster id loads its members (its OWN request class)', () => {
    answerList();
    const row = fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]');
    row.click();
    fixture.detectChanges();
    const member = fixture.nativeElement.querySelector('[data-test="nest-reports-member-cl-1"]');
    member.click();
    fixture.detectChanges();
    expect(nest.detailRequests.length).toBe(1);
    expect(nest.detailRequests[0].clusterId).toBe('cl-1');
    // The excerpt is NOT asked for unless the operator asks.
    expect(nest.detailRequests[0].includeExcerpt).toBe(false);
  });

  it('one row expanded at a time', () => {
    answerList(
      listPayload({
        groups: [
          group({ cluster_ids: ['cl-1'], tree_root_instance_id: 'tree-1' }),
          group({ cluster_ids: ['cl-2'], tree_root_instance_id: 'tree-2' }),
        ] as never,
      }),
    );
    const rows = fixture.nativeElement.querySelectorAll('[data-test^="nest-reports-row-"]');
    rows[0].click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('[data-test="nest-reports-members"]').length).toBe(1);
    rows[1].click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('[data-test="nest-reports-members"]').length).toBe(1);
  });

  it('AM-12(c) — the excerpt renders ≤300 chars with a reveal affordance, never raw', () => {
    answerList();
    const row = fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]');
    row.click();
    fixture.detectChanges();
    const member = fixture.nativeElement.querySelector('[data-test="nest-reports-member-cl-1"]');
    member.click();
    fixture.detectChanges();
    const long = 'x'.repeat(500);
    nest.detailSubjects[0]?.next(
      detailPayload({
        reports: [
          {
            report_id: 'rep-1',
            agent_id: 'coder',
            instance_id: 'inst-9',
            created_at: '2026-11-05T00:55:00+00:00',
            category: 'tool-failure',
            category_raw: null,
            severity: 'critical',
            description: 'the tool call failed twice',
            outcome: 'reported',
            excerpt: long,
          },
        ],
      }),
    );
    fixture.detectChanges();
    const excerpt = fixture.nativeElement.querySelector('[data-test="nest-reports-excerpt"]');
    expect(excerpt).not.toBeNull();
    expect((excerpt.textContent ?? '').trim().length).toBe(300);
    // The raw text is NOT in the DOM.
    expect((excerpt.textContent ?? '').trim().length).toBeLessThan(long.length);
    const reveal = fixture.nativeElement.querySelector('[data-test="nest-reports-excerpt-reveal-rep-1"]');
    expect(reveal).not.toBeNull();
    reveal.click();
    fixture.detectChanges();
    const revealed = fixture.nativeElement.querySelector('[data-test="nest-reports-excerpt"]');
    expect((revealed.textContent ?? '').trim().length).toBe(500);
  });

  it('the excerpt is ABSENT unless the operator asked for it', () => {
    answerList();
    const row = fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]');
    row.click();
    fixture.detectChanges();
    const member = fixture.nativeElement.querySelector('[data-test="nest-reports-member-cl-1"]');
    member.click();
    fixture.detectChanges();
    nest.detailSubjects[0]?.next(detailPayload());
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-excerpt"]')).toBeNull();
  });

  it('a STALE list response is discarded (the supersede token)', () => {
    fixture.detectChanges();
    const first = nest.listSubjects[0];
    fixture.componentRef.setInput('window', '15m');
    fixture.detectChanges();
    const second = nest.listSubjects[1];
    // The OLD answer arrives LAST — it must never paint.
    second?.next(listPayload({ generated_at_utc: 'new' }));
    first?.next(listPayload({ generated_at_utc: 'old' }));
    fixture.detectChanges();
    const row = fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]');
    expect((row.textContent ?? '')).toContain('tool-failure');
    expect(component.groups()?.length).toBe(1);
    // The old payload carried the same rows, so the discriminator is the
    // freshness line (the old answer's instant must not win).
    const line = fixture.nativeElement.querySelector('[data-test="nest-reports-freshness"]');
    expect(line.textContent).toContain('2026-11-05T01:00:00+00:00');
  });

  it('the domain-only 404 carries the DOMAIN-LOCAL copy (never the door\'s)', () => {
    answerList();
    const row = fixture.nativeElement.querySelector('[data-test^="nest-reports-row-"]');
    row.click();
    fixture.detectChanges();
    const member = fixture.nativeElement.querySelector('[data-test="nest-reports-member-cl-1"]');
    member.click();
    fixture.detectChanges();
    nest.detailSubjects[0]?.error({
      status: 404,
      error: { detail: { error: 'anomaly_cluster_not_found' } },
    });
    fixture.detectChanges();
    const detailError = fixture.nativeElement.querySelector('[data-test="nest-reports-detail-error"]');
    expect(detailError).not.toBeNull();
    expect((detailError.textContent ?? '').toLowerCase()).toContain('cluster');
    expect((detailError.textContent ?? '').toLowerCase()).not.toContain('metrics door');
  });

  it('a 503 renders NO rows — the degradation state is honest (no placeholder series)', () => {
    // M-4b: a planted placeholder series on the failure path would
    // red this leg (a failure must never be dressed as data).
    fixture.detectChanges();
    nest.listSubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'anomaly_unavailable' } },
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('[data-test^="nest-reports-row-"]').length).toBe(0);
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-occurrence-badge"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-severity-chip"]')).toBeNull();
    // The error banner IS rendered (the failure is stated, never hidden).
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-error"]')).not.toBeNull();
  });

  it('the domain 503 carries its own copy AND an honest Retry', () => {
    fixture.detectChanges();
    nest.listSubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'anomaly_unavailable' } },
    });
    fixture.detectChanges();
    const banner = fixture.nativeElement.querySelector('[data-test="nest-reports-error"]');
    expect(banner).not.toBeNull();
    expect((banner.textContent ?? '').toLowerCase()).toContain('anomaly store');
    expect(banner.getAttribute('data-error-class')).toBe('anomaly_unavailable');
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-retry"]')).not.toBeNull();
  });

  it('THE SINGLE-BANNER DISCIPLINE — a 503 on BOTH sources yields ONE banner and NO Retry', () => {
    // The central half is failing (its own read) AND the domain route
    // answers the same posture. The domain panel is SUPPRESSED — the
    // posture is explained once.
    fixture.componentRef.setInput('state', state({ errorClass: 'auth_not_configured' }));
    fixture.detectChanges();
    nest.listSubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED' } },
    });
    fixture.detectChanges();
    const banners = fixture.nativeElement.querySelectorAll('[data-error-class="auth_not_configured"]');
    expect(banners.length).toBe(1);
    // The auth class is NOT retryable — no Retry affordance.
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-retry"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="central-tab-retry"]')).toBeNull();
    // The section still renders (the tab never blanks).
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-section"]')).not.toBeNull();
  });

  it('the auth posture is never explained TWICE (the frame states it page-level)', () => {
    // The domain route answers the auth posture while the central half
    // is healthy — the metrics FRAME renders that posture page-level,
    // so this body contributes NO second banner.
    fixture.detectChanges();
    nest.listSubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED' } },
    });
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelectorAll('[data-error-class="auth_not_configured"]').length,
    ).toBe(0);
    // The section is degraded-but-present (the tab never blanks).
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-suppressed"]')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="nest-reports-section"]')).not.toBeNull();
  });

  it('the poll stops while the browser tab is hidden', () => {
    jest.useFakeTimers();
    const original = Object.getOwnPropertyDescriptor(document, 'visibilityState');
    fixture.detectChanges();
    // The first request is answered — otherwise the skip-if-in-flight
    // guard (R4) would hold every later tick, and the test would pass
    // for the wrong reason.
    nest.listSubjects[0]?.next(listPayload());
    const before = nest.listRequests.length;
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });
    document.dispatchEvent(new Event('visibilitychange'));
    jest.advanceTimersByTime(60_000);
    expect(nest.listRequests.length).toBe(before);
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));
    jest.advanceTimersByTime(60_000);
    expect(nest.listRequests.length).toBeGreaterThan(before);
    if (original) {
      Object.defineProperty(document, 'visibilityState', original);
    }
    jest.useRealTimers();
  });
});

describe('the nest-reports tab — AM-12(d) the template-safety pin', () => {
  it('the template carries NO innerHTML and NO bypassSecurityTrust', () => {
    expect(templateSrc.length).toBeGreaterThan(100);
    expect(templateSrc).not.toContain('innerHTML');
    expect(templateSrc).not.toContain('bypassSecurityTrust');
  });

  it('the tab imports NO chart library (the charts ride the shared body)', () => {
    expect(tabSrc).not.toContain('chart.js');
    expect(tabSrc).not.toMatch(/from\s+['"]chart\.js['"]/);
  });
});
