// The TOOL-USAGE tab pack (the tool-usage-metrics-p1 lane, phase 4 —
// phase4-plan Task 6).
//
// The load-bearing pins:
//
//   * the tab appears in the manifest with the RULED id `tool-usage` and
//     the RULED label `Tool Usage`, slot `custom` (it binds NO family —
//     that is WHY it is custom);
//   * the request shapes — the summary carries the tab's window mapped
//     onto `days` and omits `project_id` for all-projects; the daily
//     read is its OWN request class carrying the required `tool_name`;
//   * the SINGLE-BANNER DISCIPLINE — a 503 posture on BOTH sources yields
//     exactly ONE banner and NO Retry (the trend section's banner is
//     suppressed: the posture is stated once);
//   * the domain-only 404 carries the DOMAIN-LOCAL copy;
//   * the EMPTY state is DISTINCT from every error state and HONEST —
//     a failed summary never renders rows or the empty hint;
//   * the rows sort by `calls_7d` DESC, ties by `tool_name` asc;
//   * zero/near-zero rows are VISUALLY DISTINCT (row class + the ≈0
//     badge + the candidate marker);
//   * a row click expands the per-agent breakdown from the row's OWN
//     payload (NO second summary fetch);
//   * a stale response is discarded (the supersede token — one per
//     request class);
//   * the poll stops while the browser tab is hidden;
//   * AM-12(d) — the template carries NO `innerHTML` and NO
//     `bypassSecurityTrust`;
//   * THE LAZY MECHANISM — the shell's map names the THIN HOST (not the
//     heavy body), and `tabs.components.ts` statically imports NONE of
//     the heavy files.

import { readFileSync } from 'fs';
import { join } from 'path';

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Observable, Subject, of } from 'rxjs';

import { Project, ProjectListResponse } from '../../models/project.model';
import { ProjectService } from '../../services/project.service';
import { ToolUsageService } from '../../services/tool-usage.service';
import {
  ToolUsageDailySeries,
  ToolUsageSummary,
} from '../../models/tool-usage.models';
import { METRICS_TABS, metricsTabById } from './tabs.manifest';
import { ToolUsageTabComponent } from './tabs/tool-usage-tab.component';
import { ToolUsageTabHostComponent } from './tabs/tool-usage-tab-host.component';
import { METRICS_TAB_COMPONENTS } from './tabs/tabs.components';

const metricsDir = join(__dirname);
const tabSrc = readFileSync(join(metricsDir, 'tabs', 'tool-usage-tab.component.ts'), 'utf-8');
const hostSrc = readFileSync(join(metricsDir, 'tabs', 'tool-usage-tab-host.component.ts'), 'utf-8');
const mapSrc = readFileSync(join(metricsDir, 'tabs', 'tabs.components.ts'), 'utf-8');

/**
 * The tab's TEMPLATE block — the AM-12(d) pin's subject (the template,
 * not the file's prose: the component's doc comment NAMES the forbidden
 * tokens, and a file-level scan would red on the documentation).
 */
const templateSrc = (() => {
  const start = tabSrc.indexOf('template: `');
  const end = tabSrc.indexOf('`,', start);
  return start >= 0 && end > start ? tabSrc.slice(start + 'template: `'.length, end) : '';
})();

/** One project — the selector option shape (A5). */
function project(overrides: Partial<Project> = {}): Project {
  return {
    project_id: 'proj-1',
    name: 'Ensemble',
    project_type: 'software',
    status: 'active',
    main_directory: null,
    related_directories: [],
    description: null,
    tags: [],
    shortnames: [],
    metadata: {},
    relationships: {},
    creator_instance_id: null,
    creator_agent_id: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: null,
    job_queue_paused: false,
    ...overrides,
  };
}

/** One tool row — the frozen contract's shape (UNSORTED by default). */
function row(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    tool_name: 'read_file',
    tool_category: 'filesystem',
    calls_24h: 2,
    calls_7d: 41,
    calls_total: 130,
    agents: [{ agent_id: 'coder', calls_7d: 40 }],
    ...overrides,
  };
}

/** The summary payload — the route's shape. */
function summaryPayload(
  rows: readonly Record<string, unknown>[],
): ToolUsageSummary {
  return {
    generated_at: '2026-11-21T01:00:00+00:00',
    rows: rows as ToolUsageSummary['rows'],
  };
}

class StubProjectService {
  readonly listRequests = 0;
  listProjects(): Observable<ProjectListResponse> {
    return of({ projects: [project()], total: 1 });
  }
}

class StubToolUsageService {
  /** ONE subject PER request — a superseded answer is delivered to one. */
  readonly summarySubjects: Subject<ToolUsageSummary>[] = [];
  readonly dailySubjects: Subject<ToolUsageDailySeries>[] = [];
  readonly summaryRequests: { projectId?: string; days?: number }[] = [];
  readonly dailyRequests: { projectId?: string; toolName: string; days?: number }[] = [];

  summary(options: { projectId?: string; days?: number } = {}) {
    this.summaryRequests.push(options);
    const subject = new Subject<ToolUsageSummary>();
    this.summarySubjects.push(subject);
    return subject.asObservable();
  }

  daily(options: { projectId?: string; toolName: string; days?: number }) {
    this.dailyRequests.push(options);
    const subject = new Subject<ToolUsageDailySeries>();
    this.dailySubjects.push(subject);
    return subject.asObservable();
  }
}

describe('the tool-usage tab — the manifest binding (the ruled id + label + slot)', () => {
  it('the tab is registered with the RULED id, label and slot', () => {
    const entry = metricsTabById('tool-usage');
    expect(entry).not.toBeNull();
    expect(entry?.id).toBe('tool-usage');
    expect(entry?.label).toBe('Tool Usage');
    expect(entry?.slot).toBe('custom');
    expect(entry?.disposition).toBe('bound');
  });

  it('the tab binds NO family (that is WHY it is the custom slot)', () => {
    const entry = metricsTabById('tool-usage') as { familyIds?: unknown };
    expect('familyIds' in entry).toBe(false);
    // The central census is UNCHANGED by this lane.
    expect(METRICS_TABS.filter((tab) => tab.slot === 'central')).toHaveLength(7);
    expect(METRICS_TABS.filter((tab) => tab.slot === 'custom')).toHaveLength(2);
  });
});

describe('ToolUsageTabComponent — the summary source', () => {
  let fixture: ComponentFixture<ToolUsageTabComponent>;
  let component: ToolUsageTabComponent;
  let stub: StubToolUsageService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ToolUsageTabComponent],
      providers: [
        provideNoopAnimations(),
        { provide: ProjectService, useClass: StubProjectService },
        { provide: ToolUsageService, useClass: StubToolUsageService },
      ],
    }).compileComponents();
    stub = TestBed.inject(ToolUsageService) as unknown as StubToolUsageService;
    fixture = TestBed.createComponent(ToolUsageTabComponent);
    component = fixture.componentInstance;
    // ALL FIVE shell inputs — setInput on an UNDECLARED input throws, so
    // this leg is also the declared-inputs contract.
    fixture.componentRef.setInput('state', null);
    fixture.componentRef.setInput('onState', null);
    fixture.componentRef.setInput('window', '7d');
    fixture.componentRef.setInput('baseline', false);
    fixture.componentRef.setInput('onRangeChange', () => undefined);
  });

  afterEach(() => {
    fixture.destroy();
  });

  function answerSummary(
    rows: readonly Record<string, unknown>[],
    index = 0,
  ): void {
    fixture.detectChanges();
    stub.summarySubjects[index]?.next(summaryPayload(rows));
    fixture.detectChanges();
  }

  it('asks the route with the tab window mapped onto days (7d → days=7)', () => {
    fixture.detectChanges();
    expect(stub.summaryRequests.length).toBe(1);
    expect(stub.summaryRequests[0].days).toBe(7);
    // project_id OMITTED — the all-projects semantics.
    expect(stub.summaryRequests[0].projectId).toBeUndefined();
  });

  it('the 24h window maps onto days=1', () => {
    fixture.detectChanges();
    component.onWindowChange('24h');
    fixture.detectChanges();
    expect(stub.summaryRequests[1]?.days).toBe(1);
  });

  it('a project selection is forwarded; clearing back to All omits the param again', () => {
    fixture.detectChanges();
    component.onProjectChange('proj-1');
    fixture.detectChanges();
    expect(stub.summaryRequests[1]?.projectId).toBe('proj-1');
    component.onProjectChange('');
    fixture.detectChanges();
    expect(stub.summaryRequests[2]?.projectId).toBeUndefined();
  });

  it('rows render sorted by calls_7d DESC — ties by tool_name asc (deterministic)', () => {
    answerSummary([
      row({ tool_name: 'zeta', calls_7d: 5, calls_24h: 0, calls_total: 5, agents: [] }),
      row({ tool_name: 'alpha', calls_7d: 9, calls_24h: 0, calls_total: 9, agents: [] }),
      row({ tool_name: 'beta', calls_7d: 5, calls_24h: 0, calls_total: 5, agents: [] }),
    ]);
    const names = [
      ...fixture.nativeElement.querySelectorAll('[data-test^="tool-usage-row-"]'),
    ].map((el) => (el.textContent ?? '').trim());
    expect(names.length).toBe(3);
    expect(names[0]).toContain('alpha');
    expect(names[1]).toContain('beta');
    expect(names[2]).toContain('zeta');
  });

  it('zero/near-zero rows are VISUALLY DISTINCT (row class + ≈0 badge + candidate marker)', () => {
    answerSummary([
      row({ tool_name: 'busy_tool', calls_7d: 9, agents: [] }),
      row({ tool_name: 'lonely_tool', calls_7d: 2, agents: [] }),
    ]);
    const lonely = fixture.nativeElement.querySelector('[data-test="tool-usage-row-lonely_tool"]');
    expect(lonely).not.toBeNull();
    expect(lonely.className).toContain('tool-usage-row-zero');
    expect(lonely.querySelector('[data-test="tool-usage-zero-badge"]')).not.toBeNull();
    expect(lonely.querySelector('[data-test="tool-usage-candidate-badge"]')).not.toBeNull();
    const busy = fixture.nativeElement.querySelector('[data-test="tool-usage-row-busy_tool"]');
    expect(busy.className).not.toContain('tool-usage-row-zero');
    expect(busy.querySelector('[data-test="tool-usage-zero-badge"]')).toBeNull();
  });

  it('a row click expands the per-agent breakdown from the ROW payload — no second summary fetch', () => {
    answerSummary([row({ agents: [{ agent_id: 'coder', calls_7d: 40 }] })]);
    const beforeSummary = stub.summaryRequests.length;
    const rowEl = fixture.nativeElement.querySelector('[data-test="tool-usage-row-read_file"]');
    rowEl.click();
    fixture.detectChanges();
    const breakdown = fixture.nativeElement.querySelector('[data-test="tool-usage-agent-breakdown"]');
    expect(breakdown).not.toBeNull();
    expect((breakdown.textContent ?? '')).toContain('coder');
    expect((breakdown.textContent ?? '')).toContain('40');
    // The breakdown came from `row.agents` — the summary was NOT re-read.
    expect(stub.summaryRequests.length).toBe(beforeSummary);
    // The selection fetched the DAILY series (its OWN class, one call).
    expect(stub.dailyRequests.length).toBe(1);
    expect(stub.dailyRequests[0].toolName).toBe('read_file');
  });

  it('ONE breakdown at a time — clicking another row collapses the first', () => {
    answerSummary([
      row({ tool_name: 'tool_a', calls_7d: 9, agents: [] }),
      row({ tool_name: 'tool_b', calls_7d: 8, agents: [] }),
    ]);
    fixture.nativeElement.querySelector('[data-test="tool-usage-row-tool_a"]').click();
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelectorAll('[data-test="tool-usage-agent-breakdown"]').length,
    ).toBe(1);
    fixture.nativeElement.querySelector('[data-test="tool-usage-row-tool_b"]').click();
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelectorAll('[data-test="tool-usage-agent-breakdown"]').length,
    ).toBe(1);
    expect(stub.dailyRequests.length).toBe(2);
    expect(stub.dailyRequests[1].toolName).toBe('tool_b');
  });

  it('the EMPTY state is honest and DISTINCT from every error state', () => {
    answerSummary([]);
    const empty = fixture.nativeElement.querySelector('[data-test="tool-usage-empty"]');
    expect(empty).not.toBeNull();
    expect((empty.textContent ?? '')).toContain('no tool usage recorded in the window');
    expect(fixture.nativeElement.querySelector('[data-test^="tool-usage-row-"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-error"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-trend-error"]')).toBeNull();
  });

  it('a 503 posture renders NO rows and NO empty hint — the failure is stated once, no Retry', () => {
    fixture.detectChanges();
    stub.summarySubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED' } },
    });
    fixture.detectChanges();
    // Exactly ONE banner, the door class, no Retry affordance.
    const banners = fixture.nativeElement.querySelectorAll(
      '[data-error-class="auth_not_configured"]',
    );
    expect(banners.length).toBe(1);
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-retry"]')).toBeNull();
    // Honest: no table, no rows, and NEVER the empty hint on a failure.
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-table"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test^="tool-usage-row-"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-empty"]')).toBeNull();
    // The trend section is suppressed under the posture (never a second banner).
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-trend-error"]')).toBeNull();
  });

  it('the SINGLE-BANNER discipline — a posture 503 on BOTH sources yields ONE banner', () => {
    answerSummary([row()]);
    const rowEl = fixture.nativeElement.querySelector('[data-test="tool-usage-row-read_file"]');
    rowEl.click();
    fixture.detectChanges();
    expect(stub.dailyRequests.length).toBe(1);
    // Both sources answer the same posture.
    stub.summarySubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED' } },
    });
    stub.dailySubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'AUTH_NOT_CONFIGURED' } },
    });
    fixture.detectChanges();
    const banners = fixture.nativeElement.querySelectorAll(
      '[data-error-class="auth_not_configured"]',
    );
    expect(banners.length).toBe(1);
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-retry"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-trend-retry"]')).toBeNull();
    // The section still renders (the tab never blanks).
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-section"]')).not.toBeNull();
  });

  it('the domain-only 404 carries the DOMAIN-LOCAL copy (never the door\u2019s)', () => {
    answerSummary([row()]);
    const rowEl = fixture.nativeElement.querySelector('[data-test="tool-usage-row-read_file"]');
    rowEl.click();
    fixture.detectChanges();
    stub.dailySubjects[0]?.error({
      status: 404,
      error: { detail: { error: 'unknown_tool' } },
    });
    fixture.detectChanges();
    const trendError = fixture.nativeElement.querySelector('[data-test="tool-usage-trend-error"]');
    expect(trendError).not.toBeNull();
    expect((trendError.textContent ?? '').toLowerCase()).toContain('no longer known');
    expect(trendError.getAttribute('data-error-class')).toBe('unknown_tool');
    // The 404 is NOT retryable — no retry affordance on the trend banner.
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-trend-retry"]')).toBeNull();
    // The summary half is healthy — its banner is absent.
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-error"]')).toBeNull();
  });

  it('the substrate 503 carries its own copy AND an honest Retry', () => {
    answerSummary([row()]);
    const rowEl = fixture.nativeElement.querySelector('[data-test="tool-usage-row-read_file"]');
    rowEl.click();
    fixture.detectChanges();
    stub.dailySubjects[0]?.error({
      status: 503,
      error: { detail: { error: 'tool_usage_unavailable' } },
    });
    fixture.detectChanges();
    const trendError = fixture.nativeElement.querySelector('[data-test="tool-usage-trend-error"]');
    expect(trendError).not.toBeNull();
    expect((trendError.textContent ?? '').toLowerCase()).toContain('tool-usage store');
    expect(fixture.nativeElement.querySelector('[data-test="tool-usage-trend-retry"]')).not.toBeNull();
  });

  it('the trend renders for the SELECTED tool only (the house renderer, step counters)', () => {
    answerSummary([row()]);
    const rowEl = fixture.nativeElement.querySelector('[data-test="tool-usage-row-read_file"]');
    rowEl.click();
    fixture.detectChanges();
    stub.dailySubjects[0]?.next({ series: [{ day: '2026-11-20', calls: 3 }] });
    fixture.detectChanges();
    const trend = fixture.nativeElement.querySelector('[data-test="tool-usage-trend"]');
    expect(trend).not.toBeNull();
    // No selection, no chart — the hint carries the affordance instead.
    fixture.detectChanges();
    expect(stub.dailyRequests.length).toBe(1);
  });

  it('a STALE summary response is discarded (the supersede token — one per class)', () => {
    fixture.detectChanges();
    const first = stub.summarySubjects[0];
    component.onWindowChange('24h');
    fixture.detectChanges();
    const second = stub.summarySubjects[1];
    // The OLD answer arrives LAST — it must never paint.
    second?.next(summaryPayload([row({ tool_name: 'fresh_tool' })]));
    fixture.detectChanges();
    first?.next(summaryPayload([row({ tool_name: 'stale_tool' })]));
    fixture.detectChanges();
    const rowEls = [
      ...fixture.nativeElement.querySelectorAll('[data-test^="tool-usage-row-"]'),
    ];
    expect(rowEls.length).toBe(1);
    expect((rowEls[0].textContent ?? '')).toContain('fresh_tool');
    // The daily class is SEPARATE — the stale summary answer cannot
    // clobber the daily read of the selected tool.
    expect(component.selectedTool()).toBeNull();
  });

  it('the poll stops while the browser tab is hidden', () => {
    jest.useFakeTimers();
    const original = Object.getOwnPropertyDescriptor(document, 'visibilityState');
    fixture.detectChanges();
    // The first request is answered — otherwise the skip-if-in-flight
    // guard would hold every later tick, and the test would pass for
    // the wrong reason.
    stub.summarySubjects[0]?.next(summaryPayload([row()]));
    const before = stub.summaryRequests.length;
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });
    document.dispatchEvent(new Event('visibilitychange'));
    jest.advanceTimersByTime(60_000);
    expect(stub.summaryRequests.length).toBe(before);
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));
    jest.advanceTimersByTime(60_000);
    expect(stub.summaryRequests.length).toBeGreaterThan(before);
    if (original) {
      Object.defineProperty(document, 'visibilityState', original);
    }
    jest.useRealTimers();
  });

  it('the ranking caveat + the D-7 disclosure are surfaced in the UI copy', () => {
    answerSummary([row()]);
    const caveat = fixture.nativeElement.querySelector('[data-test="tool-usage-ranking-caveat"]');
    expect(caveat).not.toBeNull();
    expect((caveat.textContent ?? '')).toContain('PROXY');
    expect((caveat.textContent ?? '')).toContain('daily digest');
    expect((caveat.textContent ?? '')).toContain('rolling');
    // R4 — the 7d column never says "current".
    expect((fixture.nativeElement.querySelector('[data-test="tool-usage-table"]').textContent ?? '')).not.toContain('current');
  });

  // POSITIVE pin: the corrected caveat carries the honest frequency-only
  // ranking claim (the spec's §6.2 corrected copy). The paragraph is
  // line-wrapped in the template, so the substring check runs against a
  // whitespace-normalised copy of the text — a single-space literal
  // checked against raw textContent would be defeated by a re-wrap.
  it('the ranking caveat states that it ranks by frequency only', () => {
    answerSummary([row()]);
    const caveat = fixture.nativeElement.querySelector('[data-test="tool-usage-ranking-caveat"]');
    expect(caveat).not.toBeNull();
    const normalised = (caveat.textContent ?? '').replace(/\s+/g, ' ').trim();
    expect(normalised).toContain('ranks by frequency only');
  });

  // NEGATIVE pin: the withdrawn over-claim "is the authoritative ranking
  // surface" must not reappear. Withdrawn because there are NO per-tool
  // schema weights published today (every tool carries the 230-token
  // average fallback, so the Weight column is constant and the ranking
  // is frequency-only) and the allow-list intersection the copy names is
  // currently computed by exact string match against raw allow entries
  // (wrong for categorized tools). The corrected copy says "is the
  // authoritative day-figure and allow-list surface" instead. Normalised
  // for the same re-wrap reason as the positive pin above.
  it('the ranking caveat does NOT carry the withdrawn "authoritative ranking surface" claim', () => {
    answerSummary([row()]);
    const caveat = fixture.nativeElement.querySelector('[data-test="tool-usage-ranking-caveat"]');
    expect(caveat).not.toBeNull();
    const normalised = (caveat.textContent ?? '').replace(/\s+/g, ' ').trim();
    expect(normalised).not.toContain('is the authoritative ranking surface');
  });

  it('the heading carries the SERVER\u2019s instant (never the client clock) — M17', () => {
    // The generatedAt signal is seeded from payload.generated_at, so the
    // heading proves the timestamp is the daemon\u2019s, not the client\u2019s.
    answerSummary([row()]);
    const stamp = fixture.nativeElement.querySelector('[data-test="tool-usage-generated-at"]');
    expect(stamp).not.toBeNull();
    expect((stamp.textContent ?? '')).toContain('2026-11-21T01:00:00+00:00');
    expect((stamp.textContent ?? '')).toContain('server clock');
  });
});

describe('the tool-usage tab — the template-safety + lazy-mechanism pins', () => {
  it('AM-12(d) — the template carries NO innerHTML and NO bypassSecurityTrust', () => {
    expect(templateSrc.length).toBeGreaterThan(100);
    expect(templateSrc).not.toContain('innerHTML');
    expect(templateSrc).not.toContain('bypassSecurityTrust');
  });

  it('the tab imports NO chart library (the chart rides the shared house renderer)', () => {
    expect(tabSrc).not.toContain('chart.js');
    expect(tabSrc).not.toMatch(/from\s+['"]chart\.js['"]/);
  });

  it('THE LAZY MECHANISM — the map names the THIN HOST, never the heavy body', () => {
    expect(METRICS_TAB_COMPONENTS['tool-usage']).toBe(ToolUsageTabHostComponent);
    expect(METRICS_TAB_COMPONENTS['tool-usage']).not.toBe(ToolUsageTabComponent);
  });

  it('THE LAZY MECHANISM — tabs.components.ts statically imports NONE of the heavy files', () => {
    expect(mapSrc).not.toMatch(/import\s[^;]*from\s+['"]\.\/tool-usage-tab\.component['"]/);
    expect(mapSrc).not.toMatch(/import\s[^;]*from\s+['"].*tool-usage\.service['"]/);
    expect(mapSrc).not.toMatch(/import\s[^;]*from\s+['"].*tool-usage\.models['"]/);
    // The host import is the ONLY tool-usage import in the map file.
    const imports = [...mapSrc.matchAll(/from\s+['"]([^'"]+)['"]/g)].map((m) => m[1]);
    const toolUsageImports = imports.filter((spec) => spec.includes('tool-usage'));
    expect(toolUsageImports).toEqual(['./tool-usage-tab-host.component']);
  });

  it('THE LAZY MECHANISM — the host\u2019s ONLY heavy reference is the dynamic import', () => {
    // The dynamic import is present…
    expect(hostSrc).toContain("await import('./tool-usage-tab.component')");
    // …and no STATIC import of the heavy body / service / models exists.
    expect(hostSrc).not.toMatch(/import\s[^;]*from\s+['"]\.\/tool-usage-tab\.component['"]/);
    expect(hostSrc).not.toMatch(/import\s[^;]*from\s+['"].*tool-usage\.service['"]/);
    expect(hostSrc).not.toMatch(/import\s[^;]*from\s+['"].*tool-usage\.models['"]/);
  });

  it('THE HOST CONTRACT — all five shell inputs are declared (undeclared inputs throw)', () => {
    for (const name of ['state', 'onState', 'window', 'baseline', 'onRangeChange']) {
      expect(hostSrc).toMatch(new RegExp(`readonly ${name}\\s*=\\s*input`));
    }
    // The never-blank trio: loading placeholder, chunk error, Retry.
    expect(hostSrc).toContain('tool-usage-tab-loading');
    expect(hostSrc).toContain('tool-usage-chunk-error');
    expect(hostSrc).toContain('tool-usage-chunk-retry');
    // The zoneless mount discipline.
    expect(hostSrc).toContain('createComponent');
    expect(hostSrc).toContain('setInput');
    expect(hostSrc).toContain('detectChanges');
    expect(hostSrc).toContain('ngOnDestroy');
  });
});
