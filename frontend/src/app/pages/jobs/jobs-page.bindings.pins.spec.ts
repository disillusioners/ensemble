// Jobs page template/source pins — jobs-page-improvement arc, Phase 1.
//
// F-5-class pins: PLAIN-TS specs cannot verify DOM bindings, so every
// production write-path and filter binding is pinned against the REAL
// file text (readFileSync + __dirname). This spec is the flagship
// proof of plan task 6: every filter binding in jobs.component.html
// is enumerated and mapped 1:1 to a JobsFilterState key — provable
// from TEMPLATE wiring, not from a store spec alone — plus the
// dual-pipeline DELETION proof on the component source.

import { readFileSync } from 'fs';
import { join } from 'path';

const jobsDir = join(__dirname, '.');
const componentSrc = readFileSync(join(jobsDir, 'jobs.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(jobsDir, 'jobs.component.html'), 'utf-8');
const storeSrc = readFileSync(join(jobsDir, 'jobs-page.store.ts'), 'utf-8');
const modelSrc = readFileSync(
  join(__dirname, '../../models/jobs-filter-state.model.ts'),
  'utf-8',
);
const workModelSrc = readFileSync(
  join(__dirname, '../../models/work.model.ts'),
  'utf-8',
);
const jobServiceSrc = readFileSync(
  join(__dirname, '../../services/job.service.ts'),
  'utf-8',
);

// The enumerated filter bindings. Every entry: the template binding
// (exact production text) ↔ the JobsFilterState key it drives ↔ the
// view scope. This table IS the enumeration pin — the describe below
// proves each row against the real template text.
const FILTER_BINDINGS: Array<{
  key: string;
  viewScope: 'both' | 'queues-only';
  templateEvidence: RegExp;
  storeEvidence?: RegExp;
}> = [
  {
    key: 'project_id',
    viewScope: 'both',
    templateEvidence:
      /\[ngModel\]="filters\(\)\.project_id \|\| ''"/,
    storeEvidence: /onProjectFilterChange[\s\S]{0,400}setFilters\(\{ project_id: projectId \|\| null, queue_id: null \}\)/,
  },
  {
    key: 'view_mode',
    viewScope: 'both',
    templateEvidence: /\(change\)="onViewModeChange\(\$event\.value\)"/,
    storeEvidence: /onViewModeChange[\s\S]{0,400}setFilters\(\{ view_mode: mode \}\)/,
  },
  {
    key: 'status',
    viewScope: 'both',
    templateEvidence:
      /\[selected\]="filters\(\)\.status\.includes\(option\.value\)"/,
    storeEvidence: /onStatusFilterChange[\s\S]{0,400}setFilters\(\{ status: statuses\.length > 0 \? statuses : \[\] \}\)/,
  },
  {
    key: 'source',
    viewScope: 'queues-only',
    templateEvidence:
      /\[ngModel\]="filters\(\)\.source \|\| 'all'"/,
    storeEvidence: /onSourceFilterChange[\s\S]{0,300}setFilters\(\{ source: source === 'all' \? null : source \}\)/,
  },
  {
    key: 'agent_id',
    viewScope: 'both',
    templateEvidence:
      /\[ngModel\]="filters\(\)\.agent_id \|\| 'all'"/,
    storeEvidence: /onAgentFilterChange[\s\S]{0,300}setFilters\(\{ agent_id: agentId === 'all' \? null : agentId \}\)/,
  },
  {
    key: 'queue_id',
    viewScope: 'queues-only',
    templateEvidence: /\(queueSelected\)="onQueueSelected\(\$event\)"/,
    storeEvidence: /onQueueSelected[\s\S]{0,200}setFilters\(\{ queue_id: queueId \|\| null \}\)/,
  },
  {
    key: 'include_deleted',
    viewScope: 'queues-only',
    templateEvidence: /\(change\)="onToggleShowDeleted\(\$event\.checked\)"/,
    storeEvidence: /onToggleShowDeleted[\s\S]{0,300}setFilters\(\{ include_deleted: checked \}\)/,
  },
];

/** Every JobsFilterState key declared in the model. */
const MODEL_KEYS = [
  'status',
  'source',
  'agent_id',
  'project_id',
  'queue_id',
  'include_deleted',
  'view_mode',
];

describe('Template-source enumeration pin — filter bindings ↔ JobsFilterState keys (1:1)', () => {
  it('the model declares EXACTLY the enumerated keys (no hidden filter state)', () => {
    for (const key of MODEL_KEYS) {
      expect(modelSrc).toMatch(new RegExp(`^  ${key}[,:?]`, 'm'));
    }
    // No extra filter keys slipped into the interface without an
    // enumerated binding row.
    const interfaceBody = modelSrc.slice(
      modelSrc.indexOf('export interface JobsFilterState'),
      modelSrc.indexOf('}', modelSrc.indexOf('export interface JobsFilterState')),
    );
    const declaredKeys = interfaceBody.match(/^  (\w+)[,:?]/gm)?.map((l) => l.trim().replace(/[,:?]$/, '')) ?? [];
    expect(declaredKeys.sort()).toEqual([...MODEL_KEYS].sort());
  });

  it('EVERY enumerated binding is present in the real template text', () => {
    for (const binding of FILTER_BINDINGS) {
      expect({ key: binding.key, ok: binding.templateEvidence.test(templateSrc) })
        .toEqual({ key: binding.key, ok: true });
    }
  });

  it('EVERY binding row drives its key through the real component handler → store.setFilters', () => {
    for (const binding of FILTER_BINDINGS) {
      if (binding.storeEvidence) {
        expect({ key: binding.key, ok: binding.storeEvidence.test(componentSrc) })
          .toEqual({ key: binding.key, ok: true });
      }
    }
  });

  it('enumeration is exhaustive BOTH ways (every row unique, every key covered)', () => {
    const rowKeys = FILTER_BINDINGS.map((b) => b.key).sort();
    expect(rowKeys).toEqual([...MODEL_KEYS].sort());
    expect(new Set(rowKeys).size).toBe(rowKeys.length);
  });

  it('queue/source/show-deleted are the queues-view-scoped keys (documented scoping, not dead controls)', () => {
    const scoped = FILTER_BINDINGS.filter((b) => b.viewScope === 'queues-only').map((b) => b.key);
    expect(scoped.sort()).toEqual(['include_deleted', 'queue_id', 'source'].sort());
  });

  it('all-work view HIDES the queues-only controls with honest copy (absent-with-copy, not no-op)', () => {
    // Source select sits inside the queues-only branch…
    const sourceBranch = templateSrc.match(
      /@if \(!isAllWorkView\(\)\) \{[\s\S]{0,400}?label="Source \(window-scoped\)"[\s\S]{0,400}?\} @else \{[\s\S]{0,400}?sourceUnavailableCopy[\s\S]{0,200}?\}/,
    );
    expect(sourceBranch).not.toBeNull();
    // …and so does the Show Deleted checkbox, with explanatory copy.
    const deletedBranch = templateSrc.match(
      /@if \(!isAllWorkView\(\)\) \{[\s\S]{0,400}?onToggleShowDeleted\(\$event\.checked\)[\s\S]{0,400}?\} @else \{[\s\S]{0,400}?showDeletedUnavailableCopy[\s\S]{0,200}?\}/,
    );
    expect(deletedBranch).not.toBeNull();
    // The copy strings exist on the component.
    expect(componentSrc).toMatch(/sourceUnavailableCopy\s*=/);
    expect(componentSrc).toMatch(/showDeletedUnavailableCopy\s*=/);
  });

  it('window-scoped labels are on the real controls', () => {
    expect(templateSrc).toContain('label="Source (window-scoped)"');
    expect(templateSrc).toContain('label="Agent (window-scoped)"');
  });

  it('the template list renders EXACTLY ONE projection (no view-mode branch in the loop)', () => {
    // Phase 2 — the list moved from ``@for (job of ...)`` to
    // ``cdk-virtual-scroll-viewport`` + ``*cdkVirtualFor`` over the
    // flattened ``renderRows()`` (WindowItem[]). The projection
    // source is ONE (the store's ``filteredJobs`` projected through
    // ``renderRows``), not two view-mode branches. Pin anchors on
    // the REAL production text.
    const loop = templateSrc.match(/\*cdkVirtualFor="let item of (\w+)\(\); trackBy: trackByKey"/);
    expect(loop).not.toBeNull();
    expect(loop![1]).toBe('renderRows');
    // Exactly one ``*cdkVirtualFor`` over the list, no second
    // branch — same invariant the pre-Phase-2 ``@for`` pin held.
    expect(templateSrc.match(/\*cdkVirtualFor=/g)).toHaveLength(1);
  });

  it('virtual-scroll track-by keys on item.key (job_id for rows; Phase 3 missionId for headers)', () => {
    // Plan task 4 — track by job_id. The page-level trackBy
    // helper returns ``item.key`` (which IS ``job.job_id`` for
    // rows, and which Phase 3 will set to the missionId for
    // header items). The bind is the page's trackByKey, not
    // Angular's default identity.
    expect(componentSrc).toMatch(/protected trackByKey = \(_index: number, item: WindowItem\): string => item\.key/);
    expect(templateSrc).toMatch(/trackBy: trackByKey/);
  });
});

describe('F-5 production-source pins — the store OWNS the writes', () => {
  it('store write-sites exist on the REAL store file (payload set + degrade flags + patch paths)', () => {
    expect(storeSrc).toMatch(/this\.jobs\.set\(jobs\)/);
    expect(storeSrc).toMatch(/this\.works\.set\(works\)/);
    expect(storeSrc).toMatch(/this\.jobsDegraded\.set\(false\)/);
    expect(storeSrc).toMatch(/this\.worksDegraded\.set\(false\)/);
    expect(storeSrc).toMatch(/this\.jobsDegraded\.set\(true\)/);
    expect(storeSrc).toMatch(/this\.worksDegraded\.set\(true\)/);
    // Retain-last-data: the error branches contain NO payload reset.
    const errorBranch = storeSrc.match(
      /error: \(err\) => \{[\s\S]{0,300}?jobsDegraded\.set\(true\)[\s\S]{0,300}?\}/,
    );
    expect(errorBranch).not.toBeNull();
    expect(errorBranch![0]).not.toContain('this.jobs.set');
    const workErrorBranch = storeSrc.match(
      /error: \(err\) => \{[\s\S]{0,300}?worksDegraded\.set\(true\)[\s\S]{0,300}?\}/,
    );
    expect(workErrorBranch).not.toBeNull();
    expect(workErrorBranch![0]).not.toContain('this.works.set');
  });

  it('store SSE patch and mutation seams exist on the REAL store file', () => {
    expect(storeSrc).toMatch(/updateJobFromSse\(status: JobEventPayload\)/);
    expect(storeSrc).toMatch(/this\.jobs\.update\(jobs =>/);
    expect(storeSrc).toMatch(/this\.works\.update\(works =>/);
    expect(storeSrc).toMatch(/removeJob\(jobId: string\)/);
    expect(storeSrc).toMatch(/patchJob\(jobId: string, patch: Partial<Job>\)/);
  });

  it('store P-A contract: root_only:false inside toWorkFilters (real model file text)', () => {
    expect(modelSrc).toMatch(/root_only: false/);
    expect(storeSrc).toMatch(/fetchWorks\(\): void/);
    expect(storeSrc).toMatch(/this\.fetchers\.fetchWorks\(toWorkFilters\(this\.filterState\(\)\)\)/);
  });

  it('limit=100 ships on the REAL JobService.listJobs (explicit newest-100 window)', () => {
    // Source-text pin: the params construction starts with the explicit
    // limit — behavioral URL pins live in the service spec's mirror;
    // THIS pin is the production-text anchor per F-5.
    expect(jobServiceSrc).toMatch(/let params = new HttpParams\(\)\s*\n\s*\/\/ P1 — explicit newest-100 window[\s\S]{0,200}?\.set\('limit', '100'\)/);
    expect(jobServiceSrc).not.toMatch(/return of\(\[\]\)/); // swallow-to-empty retired
  });

  it('workToJob row parity lives in the REAL work model (Timeline fields carried, not nulled)', () => {
    expect(workModelSrc).toMatch(/export function workToJob\(work: Work\): Job \{/);
    expect(workModelSrc).toMatch(/started_at: work\.started_at \?\? null/);
    expect(workModelSrc).toMatch(/completed_at: work\.completed_at \?\? null/);
    // The Work wire model carries the parity fields.
    expect(workModelSrc).toMatch(/started_at\?: string \| null;/);
    expect(workModelSrc).toMatch(/completed_at\?: string \| null;/);
  });
});

describe('Dual-pipeline DELETION proof (jobs.component.ts / .html)', () => {
  it('the component has NO code references to the deleted all-work mapper', () => {
    expect(componentSrc).not.toMatch(/private\s+worksAsJobs/);
    expect(componentSrc).not.toMatch(/private\s+workToJob/);
    expect(componentSrc).not.toMatch(/this\.worksAsJobs\(\)/);
    expect(componentSrc).not.toMatch(/this\.workToJob\(/);
  });

  it('the component has NO local filteredJobs computed (the store owns the pipeline)', () => {
    expect(componentSrc).not.toMatch(/readonly filteredJobs\s*=\s*computed/);
    // The ONLY filteredJobs references are the store projection alias.
    expect(componentSrc).toMatch(/readonly displayedJobs = this\.store\.filteredJobs;/);
  });

  it('the component does NOT branch displayedJobs on view mode (no second path)', () => {
    expect(componentSrc).not.toMatch(/displayedJobs = computed<Job\[\]>/);
    expect(componentSrc).not.toMatch(/viewMode\(\) === 'all-work'\s*\?\s*this\.worksAsJobs/);
  });

  it('the component has NO loadJobs/loadWorks fetch bodies (legs live in the store)', () => {
    expect(componentSrc).not.toMatch(/private loadJobs\(\): void/);
    expect(componentSrc).not.toMatch(/private loadWorks\(\): void/);
    expect(componentSrc).not.toMatch(/this\.jobService\.listJobs\(this\.filters\(\)\)\.subscribe/);
    expect(componentSrc).toMatch(/fetchJobs: \(filters\) => this\.jobService\.listJobs\(filters\)/);
    expect(componentSrc).toMatch(/fetchWorks: \(filters\) => this\.workService\.getWork\(filters\)/);
  });

  it('the component has NO local SSE patch body (moved verbatim to the store)', () => {
    expect(componentSrc).not.toMatch(/private updateJobFromSse\(/);
    expect(componentSrc).toMatch(/this\.store\.updateJobFromSse\(latestStatus\)/);
  });

  it('the template never bypasses the pipeline for the all-work view', () => {
    // The all-work empty/error states are the ONLY view-mode branches
    // in the template; the LIST itself renders from one projection.
    expect(templateSrc).not.toMatch(/@for[\s\S]*worksAsJobs/);
    expect(templateSrc).not.toMatch(/workToJob/);
  });

  // ── Phase 2 — banner + render-guard + poll-gate + expansion ─────
  //
  // F-5 class pins: every NEW write-path / binding that ships in
  // Phase 2 must be pinned against the REAL production source. The
  // behavior pins (truth tables, fixtures AT/PAST caps) live in the
  // pure-model specs; THIS describe is the source-text anchor that
  // prevents drift between the spec and the production.

  it('window-honesty banner is mounted in BOTH view modes (banner region + aria-live + Reload)', () => {
    // Plan task 2 — banner region is ``aria-live="polite"`` and the
    // Reload button carries an explicit accessible label.
    expect(templateSrc).toMatch(/<div[\s\S]*?class="window-banner"[\s\S]*?aria-live="polite"/);
    expect(templateSrc).toMatch(/\[attr\.aria-label\]="reloadAccessibleLabel"/);
    expect(templateSrc).toMatch(/\(click\)="onReloadBanner\(\)"/);
  });

  it('banner state is driven by the pure window model + store degraded flag', () => {
    // The banner mount condition is ``windowBanner() === 'visible'
    // || windowDegraded()`` — the template binds both. The policy
    // (``windowIsFull``) lives in the model.
    expect(templateSrc).toMatch(/windowBanner\(\) === 'visible'/);
    expect(templateSrc).toMatch(/windowDegraded\(\)/);
    expect(componentSrc).toMatch(/readonly windowBanner = this\.store\.windowBanner/);
    expect(componentSrc).toMatch(/readonly windowDegraded = this\.store\.windowDegraded/);
  });

  it('render-guard truncation notice wires the pure renderGuard + the "switch to Queues" affordance', () => {
    // Plan task 3 — guard fires at RENDER time over the template-bound
    // projected rows (not the fetch payload); the notice carries a
    // "switch to Queues view" affordance.
    expect(templateSrc).toMatch(/@if \(truncationNotice\(\); as notice\)/);
    expect(templateSrc).toMatch(/class="render-guard-notice"/);
    expect(templateSrc).toMatch(/\(click\)="onSwitchToQueuesView\(\)"/);
    expect(componentSrc).toMatch(/readonly renderGuardOutcome = computed/);
    expect(componentSrc).toMatch(/renderGuard\(this\.windowItems\(\)\)/);
  });

  it('expansion state is keyed by job_id in the parent (survives virtual recycle)', () => {
    // Plan task 4 — DOM-local state would reset on recycle. The
    // parent owns a Set<job_id>; the card's ``expanded`` input
    // + ``expandToggle`` output wire it.
    expect(componentSrc).toMatch(/readonly expandedJobIds = signal<Set<string>>\(new Set\(\)\)/);
    expect(componentSrc).toMatch(/onToggleExpansion\(jobId: string\)/);
    expect(componentSrc).toMatch(/isCardExpanded\(item: WindowItem\)/);
    expect(templateSrc).toMatch(/\[expanded\]="isCardExpanded\(item\)"/);
    expect(templateSrc).toMatch(/\(expandToggle\)="onToggleExpansion\(item\.job\.job_id\)"/);
  });

  it('poll gate is wired through shouldTick (visibility + drawer + modal + fetchInFlight)', () => {
    // Plan task 5 — the 30s tick consults shouldTick BEFORE making
    // the HTTP call. The four gate inputs are the four pause
    // conditions.
    expect(componentSrc).toMatch(/POLL_INTERVAL_MS/);
    expect(componentSrc).toMatch(/shouldTick\(\{/);
    expect(componentSrc).toMatch(/tabVisible: this\.tabVisible\(\)/);
    expect(componentSrc).toMatch(/drawerOpen: this\.drawerOpen\(\)/);
    expect(componentSrc).toMatch(/modalOpen: this\.modalOpen\(\)/);
    expect(componentSrc).toMatch(/fetchInFlight: this\.fetchInFlight\(\)/);
  });

  it('visibilitychange listener is attached in ngOnInit and detached in ngOnDestroy (no leak)', () => {
    // The listener MUST be removed on destroy; without the
    // ``removeEventListener`` the listener survives and fires after
    // the component is gone (memory + correctness leak).
    expect(componentSrc).toMatch(/this\.doc\.addEventListener\('visibilitychange', this\.onVisibilityChange\)/);
    expect(componentSrc).toMatch(/this\.doc\.removeEventListener\('visibilitychange', this\.onVisibilityChange\)/);
  });

  it('refocus-immediate refresh is debounced (storm mitigation)', () => {
    // Plan task 5 risk — rapid tab switching would storm the BE.
    // The refocus path goes through a setTimeout(REFOCUS_DEBOUNCE_MS)
    // and a new focus cancels the pending timer.
    expect(componentSrc).toMatch(/REFOCUS_DEBOUNCE_MS/);
    expect(componentSrc).toMatch(/clearTimeout\(this\.refocusTimer\)/);
    expect(componentSrc).toMatch(/setTimeout\(\(\) => \{[\s\S]{0,300}?shouldTick/);
  });

  it('modal-open gate tracks the page-owned dialogs (create / cleanup / cancel confirm)', () => {
    // Each dialog open sets ``modalOpen = true`` BEFORE the open
    // call and clears it in ``afterClosed`` — belt + braces against
    // dialog leaks (if afterClosed fails to fire, the gate stays
    // shut — a safer failure mode than staying open).
    expect(componentSrc).toMatch(/this\.modalOpen\.set\(true\);[\s\S]{0,200}?this\.dialog\.open\(JobCreateDialogComponent/);
    expect(componentSrc).toMatch(/this\.modalOpen\.set\(true\);[\s\S]{0,200}?this\.dialog\.open\(SystemCleanupConfirmDialogComponent/);
    expect(componentSrc).toMatch(/this\.modalOpen\.set\(true\);[\s\S]{0,200}?this\.dialog\.open<ConfirmDialogComponent/);
    expect(componentSrc).toMatch(/this\.modalOpen\.set\(false\);[\s\S]{0,200}?if \(result\)/);
  });

  it('empty-state classifier is wired (loading/dataEmpty/filterEmpty/errored)', () => {
    // Plan task 8 — skeleton ONLY for first fetch; the classifier
    // is the single source of empty-state truth.
    expect(componentSrc).toMatch(/readonly emptyStateKind = computed<JobsEmptyStateKind>/);
    expect(componentSrc).toMatch(/classifyJobsEmptyState\(/);
    expect(templateSrc).toMatch(/@if \(showLoadingSkeleton\(\)\)/);
    expect(templateSrc).toMatch(/@if \(showEmptyState\(\)\)/);
    expect(templateSrc).toMatch(/\{\{ emptyStateCopy\(\)\.title \}\}/);
  });

  it('WorkService retain-last-data fix is live (errors propagate, no swallow-to-empty)', () => {
    // Plan task 6 — pre-Phase-2 swallowed errors via
    // ``catchError → of([])``, which the store's ``.next`` arm
    // treated as honest empty data and wiped the previous payload.
    // The real service now propagates via ``throwError`` so the
    // store's ``.error`` arm flips ``worksDegraded`` and retains
    // the last good list. The mirror parity pin in
    // ``work.service.spec.ts`` anchors this contract; THIS pin is
    // the production-source cross-check from the page's
    // perspective.
    const workServiceSrc = readFileSync(
      join(__dirname, '../../services/work.service.ts'),
      'utf-8',
    );
    expect(workServiceSrc).toMatch(/return throwError\(\(\) => err\)/);
    expect(workServiceSrc).not.toMatch(/return of\(\[\] as Work\[\]\)/);
  });

  it('panel + indicator surfaces stay untouched (Plan non-goal #1)', () => {
    // Plan non-goal #1 — the header panel/indicator owns the
    // glanceable/live-status role. Phase 2 must NOT touch it. The
    // components are referenced by selector; this grep proves no
    // P2 write-path snuck into them.
    const componentDir = join(__dirname, '../../components');
    // Re-read the component directory in case the test runner has
    // cached the file content (Node caches are per-process).
    const fs = require('fs');
    const path = require('path');
    const indicatorDir = path.join(componentDir, 'job-queue-indicator');
    const panelDir = path.join(componentDir, 'job-queue-panel');
    // Defensive: if the dirs don't exist (e.g. renamed), skip — the
    // plan explicitly bans edits and a missing dir is a stronger
    // invariant than a non-match.
    if (fs.existsSync(indicatorDir)) {
      const indicatorFiles = fs.readdirSync(indicatorDir);
      expect(indicatorFiles.length).toBeGreaterThan(0);
      // No Phase 2 addons — the indicator's exports list is the
      // legacy set (the file's existence IS the cross-seam
      // guarantee).
      for (const file of indicatorFiles) {
        if (file.endsWith('.ts')) {
          const src = fs.readFileSync(path.join(indicatorDir, file), 'utf-8');
          expect(src).not.toMatch(/window-banner|cdk-virtual-scroll|jobs-window|jobs-poll|jobs-empty-state/);
        }
      }
    }
    if (fs.existsSync(panelDir)) {
      const panelFiles = fs.readdirSync(panelDir);
      for (const file of panelFiles) {
        if (file.endsWith('.ts')) {
          const src = fs.readFileSync(path.join(panelDir, file), 'utf-8');
          expect(src).not.toMatch(/window-banner|cdk-virtual-scroll|jobs-window|jobs-poll|jobs-empty-state/);
        }
      }
    }
  });
});
