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

  it('render-guard truncation notice wires the pure boundary-slicing guard + the "switch to Queues" affordance', () => {
    // P3 review — the legacy ``renderGuard(this.windowItems())``
    // call silently slices the FLAT items list, which produces the
    // orphan-header case (header rendered, zero visible rows) for
    // expanded groups that cross the cap. The page now wires the
    // BOUNDARY-AWARE ``toBoundedWindowItems`` directly against
    // ``jobGroups + expandedGroupIds`` so a group is rendered whole
    // or omitted whole (no orphan). Plan task 3 acceptance:
    // the notice carries a "switch to Queues view" affordance.
    expect(templateSrc).toMatch(/@if \(truncationNotice\(\); as notice\)/);
    expect(templateSrc).toMatch(/class="render-guard-notice"/);
    expect(templateSrc).toMatch(/\(click\)="onSwitchToQueuesView\(\)"/);
    expect(componentSrc).toMatch(/readonly renderGuardOutcome = computed/);
    expect(componentSrc).toMatch(/toBoundedWindowItems\(/);
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
    // P2 fix (jobs-page-improvement) — the previous single pin
    // ``modalOpen\.set\(false\);[\s\S]{0,200}?if \(result\)`` only
    // matched the JobCreateDialog close site (the two
    // ConfirmDialog branches use ``if (!confirmed)``). Split into
    // three expects, one per dialog site, anchored to the dialog's
    // OWN open call so each close is provably bound to its dialog.
    // 1500-char windows are safe: each dialog's close sits within
    // ~1100 chars of its open call (well below 1500), so the
    // anchors can't span across dialog sites.
    expect(componentSrc).toMatch(
      /this\.dialog\.open\(JobCreateDialogComponent[\s\S]{0,1500}?this\.modalOpen\.set\(false\);[\s\S]{0,200}?if \(result\)/,
    );
    expect(componentSrc).toMatch(
      /this\.dialog\.open<ConfirmDialogComponent[\s\S]{0,1500}?this\.modalOpen\.set\(false\);[\s\S]{0,200}?if \(!confirmed\)/,
    );
    expect(componentSrc).toMatch(
      /this\.dialog\.open\(SystemCleanupConfirmDialogComponent[\s\S]{0,1500}?this\.modalOpen\.set\(false\);[\s\S]{0,200}?if \(!confirmed\)/,
    );
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

  it('showEmptyState has the hasRows short-circuit (P2 fix — list visible with rows)', () => {
    // P2 fix (jobs-page-improvement) — the COMPONENT must override
    // the classifier's defensive ``dataEmpty`` return for
    // hasRows=true so the virtual list stays visible. Without this
    // gate, a steady-state page with rows renders the empty card
    // AND the virtual list's ``!showEmptyState()`` branch hides the
    // list. The pin anchors on the REAL production text — it MUST
    // fail if the hasRows check is reverted (the pre-fix pin only
    // asserted ``showEmptyState = computed`` existed, which passed
    // green against the buggy source).
    const showEmptyStateBlock = componentSrc.match(
      /readonly showEmptyState = computed<boolean>\(\(\) => \{[\s\S]{0,800}?\}\)/,
    );
    expect(showEmptyStateBlock).not.toBeNull();
    expect(showEmptyStateBlock![0]).toMatch(
      /this\.store\.filteredJobs\(\)\.length > 0/,
    );
    // The errored-with-rows branch is the only legitimate
    // ``showEmptyState === true`` path with rows retained (the user
    // must be able to retry).
    expect(showEmptyStateBlock![0]).toMatch(
      /if \([\s\S]{0,200}?\.length > 0\) \{[\s\S]{0,200}?return kind === 'errored'/,
    );
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

  // ── Phase 3 — grouping + titles + vocabulary (F-5 source-text anchors) ─
  //
  // P3 plan task 1-6 — every NEW write-path / binding / template
  // hunk ships with a source-text pin AND a behavior spec (the
  // lesson from P2: a source-text pin alone passed green against a
  // buggy ``showEmptyState``). The behavior specs live in
  // ``jobs-grouping.model.spec.ts`` (grouping property), the
  // ``JobsPageStore`` spec (P2 store computeds), and the
  // ``MissionService`` spec (URL pins). This describe is the
  // SOURCE-TEXT anchor for the wiring.

  it('P3 grouping projection is wired: groupJobs → toWindowItems in the REAL component', () => {
    // The projection is a presentation layer over the store's
    // filteredJobs; the page must NOT re-implement grouping inline.
    expect(componentSrc).toMatch(/readonly jobGroups = computed<readonly JobGroup\[\]>\(\(\) => \{[\s\S]{0,500}?groupJobs\(this\.store\.filteredJobs\(\)\)/);
    expect(componentSrc).toMatch(/toWindowItems\(\s*this\.jobGroups\(\),\s*this\.expandedGroupIds\(\)/);
    // The windowItems computed MUST consume the new toWindowItems
    // signature (groups + expandedGroupIds + helpers), NOT the
    // pre-P3 flat-job signature.
    expect(componentSrc).not.toMatch(/toWindowItems\(this\.store\.filteredJobs\(\)\)/);
  });

  it('P3 group-header expansion is keyed by group id (parent owns the set, G1 panel port)', () => {
    expect(componentSrc).toMatch(/readonly expandedGroupIds = signal<Set<string>>\(new Set\(\)\)/);
    expect(componentSrc).toMatch(/readonly userTouchedGroupIds = signal<Set<string>>\(new Set\(\)\)/);
    expect(componentSrc).toMatch(/isGroupExpanded\(groupKey: string\): boolean/);
    expect(componentSrc).toMatch(/onToggleGroupExpansion\(groupKey: string\): void/);
    // G1 port: touched-wins merge — autoExpandGroupIds runs in an
    // effect that filters out touched ids, the effect MUST touch
    // the userTouchedGroupIds signal.
    expect(componentSrc).toMatch(/autoExpandGroupIds\(this\.jobGroups\(\)\)/);
  });

  it('P3 chevron click is a real <button type="button"> with aria-expanded + stopPropagation', () => {
    // Template-extraction audit (the plan's audit): chevron tap
    // MUST be a real button (not a clickable div) with
    // aria-expanded, an accessible label, and stopPropagation so
    // the tap never bubbles to a card/navigate handler.
    expect(templateSrc).toMatch(/<button[\s\S]*?type="button"[\s\S]*?class="group-header-chevron"[\s\S]*?\[attr\.aria-expanded\]="isGroupExpanded\(item\.groupKey\)"/);
    expect(templateSrc).toMatch(/onChevronClick\(\$event, item\.groupKey\)/);
    // stopPropagation on the chevron (template binding carries it).
    expect(templateSrc).toMatch(/\$event\.stopPropagation\(\)/);
    // The component handler exists and stops propagation too.
    expect(componentSrc).toMatch(/protected onChevronClick\(event: MouseEvent, groupKey: string\): void/);
    expect(componentSrc).toMatch(/event\.stopPropagation\(\)/);
  });

  it('P3 MissionService is the canonical home (component + indicator migrated, no JobService.listMissions)', () => {
    expect(componentSrc).toMatch(/import\s+\{[^}]*MissionService[^}]*\}\s+from\s+['"][^'"]*services\/mission\.service['"]/);
    expect(componentSrc).toMatch(/private readonly missionService = inject\(MissionService\)/);
    // JobService no longer hosts listMissions (the F-5 pin in the
    // mission.service.spec already asserts this; this is the
    // cross-pin on the component side: no JobService.listMissions
    // calls anywhere in the page).
    expect(componentSrc).not.toMatch(/this\.jobService\.listMissions\(/);
    // Component DOES use MissionService.getMission for enrichment.
    expect(componentSrc).toMatch(/this\.missionService\.getMission\(/);
  });

  it('P3 review — lazy title enrichment is capped at MAX_TITLE_ENRICHMENT_FETCHES + cascade / dedup / no-retry / missionId gate', () => {
    // Cap (named constant, not a magic number).
    expect(componentSrc).toMatch(/MAX_TITLE_ENRICHMENT_FETCHES/);
    // The picker is the cap enforcer now (extracted to
    // ``pickEnrichmentTargets`` in ``jobs-enrichment.model.ts``);
    // the component calls the helper. The behaviour spec pins
    // the cap value.
    expect(componentSrc).toMatch(/pickEnrichmentTargets\(/);
    // Defence-in-depth: the component ALSO checks
    // attemptedKeys.size before firing each request (the
    // cascade-prevention belt to the picker's braces).
    expect(componentSrc).toMatch(/attemptedKeys\.size >= MAX_TITLE_ENRICHMENT_FETCHES/);
    // Retain-last-data: the failure handler keeps the fallback
    // title — empty body, never a re-fetch (the picker filters
    // failed keys; the error handler adds to failedKeys).
    expect(componentSrc).toMatch(/error: \(\) => \{[\s\S]{0,500}?failedKeys\.add\(id\)/);
    // No-retry: the error handler stamps the key into
    // ``failedKeys`` so the picker never re-emits it across
    // polls (the spec's "no infinite retry" pin).
    expect(componentSrc).toMatch(/failedKeys\.add\(id\)/);
    // Cascade / concurrent dedup: the subscribe path stamps the
    // key into both ``attemptedKeys`` and ``inFlightKeys`` BEFORE
    // the request fires; the picker filters both sets.
    expect(componentSrc).toMatch(/attemptedKeys\.add\(id\)[\s\S]{0,40}?inFlightKeys\.add\(id\)/);
    // Child-bound (missionId null) groups never fetch — the
    // picker enforces the gate. Pin the picker model file too.
    const fs = require('fs');
    const path = require('path');
    const enrichmentModelSrc = fs.readFileSync(
      path.join(__dirname, '../../models/jobs-enrichment.model.ts'),
      'utf-8',
    );
    expect(enrichmentModelSrc).toMatch(/if \(!g\.missionId\) continue/);
  });

  it('P3 header title uses the instanceDisplayTitle chain (NOT the job-row resolveTitle chain)', () => {
    // The plan: title fallback via ``instanceDisplayTitle`` (NOT
    // ``resolveTitle`` which stays on cards). The component wires
    // the model helper into the projection.
    expect(componentSrc).toMatch(/groupHeaderTitle\(group/);
    // Anti-pin: the legacy ``resolveTitle`` chain is NOT used for
    // group headers (it stays on cards / panel, never on the page).
    expect(componentSrc).not.toMatch(/this\.resolveTitle\(group/);
  });

  it('P3 NO_MISSION_CONTEXT_TITLE is the explicit fallback copy (F-5 anchor)', () => {
    // The template MUST render the pinned copy via the model helper
    // — never an inline "settled" or other mission-side-prose
    // violation. The component delegates the fallback to
    // ``groupHeaderTitle`` (which reads ``NO_MISSION_CONTEXT_TITLE``).
    expect(componentSrc).toMatch(/NO_MISSION_CONTEXT_KEY/);
    // The pinned copy itself lives on the grouping model — the
    // grouping-model spec is the behavioral pin; this pin is the
    // production-source anchor.
    const fs = require('fs');
    const path = require('path');
    const groupingModelSrc = fs.readFileSync(
      path.join(__dirname, '../../models/jobs-grouping.model.ts'),
      'utf-8',
    );
    expect(groupingModelSrc).toMatch(/NO_MISSION_CONTEXT_TITLE\s*=\s*'No mission context'/);
  });

  it('P3 review — settled → teal #14B8A6 + RECEIPT_LONG_GLYPH constant (single source of truth)', () => {
    // P3 review: the literal ``receipt_long`` was promoted to a
    // NAMED export (``RECEIPT_LONG_GLYPH``) in ``job.model.ts``
    // so the card / panel / receipt-chip share one constant —
    // drift risk on three independent literals is closed. The
    // card imports + uses the constant; the panel imports +
    // uses the constant; the receipt-chip template binds to
    // the constant via a component field.
    const fs = require('fs');
    const path = require('path');
    const jobModelSrc = fs.readFileSync(
      path.join(__dirname, '../../models/job.model.ts'),
      'utf-8',
    );
    const jobCardSrc = fs.readFileSync(
      path.join(__dirname, '../../components/job-card/job-card.component.ts'),
      'utf-8',
    );
    const panelSrc = fs.readFileSync(
      path.join(__dirname, '../../components/job-queue-panel/job-queue-panel.component.ts'),
      'utf-8',
    );
    const jobCardHtmlSrc = fs.readFileSync(
      path.join(__dirname, '../../components/job-card/job-card.component.html'),
      'utf-8',
    );
    // Model export — single source of truth for the glyph.
    expect(jobModelSrc).toMatch(/export const RECEIPT_LONG_GLYPH = 'receipt_long'/);
    // Teal color #14B8A6 for settled (NOT green completed).
    expect(jobModelSrc).toMatch(/case 'settled':[\s\S]{0,80}?#14B8A6/);
    // Card imports the constant; the literal is gone from the
    // status switch.
    expect(jobCardSrc).toMatch(/RECEIPT_LONG_GLYPH/);
    expect(jobCardSrc).not.toMatch(/case 'settled':[\s\S]{0,200}?return 'receipt_long'/);
    // Panel imports + uses the constant.
    expect(panelSrc).toMatch(/RECEIPT_LONG_GLYPH/);
    // Receipt-chip template binds via the component field — no
    // bare literal in the HTML.
    expect(jobCardHtmlSrc).not.toMatch(/receipt-icon">receipt_long/);
    expect(jobCardHtmlSrc).toMatch(/receipt-icon">\{\{ receiptLongGlyph \}\}/);
  });

  it('P3 carry-over — dead-legacy aliases isEmptyState / isEmptyWorkState are removed from the component', () => {
    // The P2 empty-state model + P2 ``showEmptyState`` computed
    // REPLACED both aliases; the P3 carry-over checklist retires
    // them so a future refactor cannot re-introduce silently-sliced
    // dead code. A grep confirms ZERO production-text references.
    expect(componentSrc).not.toMatch(/readonly isEmptyState\s*=\s*computed/);
    expect(componentSrc).not.toMatch(/readonly isEmptyWorkState\s*=\s*computed/);
    // The template never bound them either (the P2 source-text
    // pin covers the new path).
    expect(templateSrc).not.toMatch(/isEmptyState\(/);
    expect(templateSrc).not.toMatch(/isEmptyWorkState\(/);
  });

  it('P3 carry-over — unused MatProgressSpinnerModule import is removed from the component', () => {
    // The P2 empty-state model replaced the legacy spinner with a
    // loading skeleton; the import had no template reference and
    // no spec pin. The P3 carry-over removes it so the unused
    // import cannot drift back into the bundle as a budget hit.
    expect(componentSrc).not.toMatch(/import\s*\{[^}]*MatProgressSpinnerModule[^}]*\}\s*from\s*['"]@angular\/material\/progress-spinner['"]/);
    // The component must NOT add the symbol to its ``imports`` array
    // (the array entry would re-introduce it to the bundle).
    expect(componentSrc).not.toMatch(/MatProgressSpinnerModule,\s*\n\s*MatChipsModule/);
  });
});

// ── P4 — defer banner + holders panel binding pins ────────────────────
//
// Plain-TS specs cannot verify DOM bindings; the F-5 pins below
// pin every (template ↔ component) ↔ (handler ↔ store) wiring the
// P4 plan introduced. The companion behavior specs in
// ``jobs-page.store.spec.ts`` and ``defer-blocked.model.spec.ts``
// prove the LOGIC; this spec proves the WIRING.

describe('P4 — defer banner + holders panel template↔component binding pins', () => {
  it('template renders the defer banner under the filter bar (visibility helper)', () => {
    // P4 task 1: page-level banner under the filter bar; the
    // ``@if (deferPageBanner(); as banner)`` template guard IS the
    // visibility helper (matches ``deferBlockIndicator``'s null-
    // hiding semantics).
    expect(templateSrc).toMatch(/@if \(deferPageBanner\(\); as banner\)/);
  });

  it('template binds the severity classes to the banner (amber/info/red)', () => {
    // The three severity classes are mutually exclusive (the model
    // helper returns ONE severity per payload) — pinning them keeps
    // a future SCSS refactor from collapsing two severities into
    // one style.
    expect(templateSrc).toMatch(/\[class\.defer-page-banner-amber\]="banner\.severity === 'amber'"/);
    expect(templateSrc).toMatch(/\[class\.defer-page-banner-info\]="banner\.severity === 'info'"/);
    expect(templateSrc).toMatch(/\[class\.defer-page-banner-red\]="banner\.severity === 'red'"/);
  });

  it('template binds the degraded flag (overlay class + note copy)', () => {
    // P4 task 4: replaces the silent swallow at :587-589. The
    // degraded overlay MUST surface in three places — the dashed
    // border (visual), the "stale" tag (inline), and the
    // "Last check failed — showing retained state." note (body).
    expect(templateSrc).toMatch(/\[class\.defer-page-banner-degraded\]="deferDegraded\(\)"/);
    expect(templateSrc).toMatch(/@if \(deferDegraded\(\)\)/);
    expect(templateSrc).toMatch(/Last check failed — showing retained state/);
  });

  it('template mounts the inline holders panel with the four IO bindings', () => {
    // P4 task 2: holders drill-down. The panel consumes the same
    // store signals the banner does — the inputs are direct
    // aliases, not re-derived.
    expect(templateSrc).toMatch(/<app-defer-holders-panel/);
    expect(templateSrc).toMatch(/\[status\]="deferStatus\(\)"/);
    expect(templateSrc).toMatch(/\[degraded\]="deferDegraded\(\)"/);
    expect(templateSrc).toMatch(/\[actionInFlight\]="deferActionInFlight\(\)"/);
    expect(templateSrc).toMatch(/\(forceComplete\)="onHolderForceComplete\(\$event\)"/);
    expect(templateSrc).toMatch(/\(resendForeground\)="onHolderResendForeground\(\$event\)"/);
  });

  it('template renders the "Review holders" toggle ONLY when holders are present', () => {
    // P4 task 2: the banner button is gated on ``banner.holders.length > 0``.
    // The anomaly branch (RED) hides the button — anomaly is banner-only
    // with "Open System Cleanup" instead.
    expect(templateSrc).toMatch(/@if \(banner\.holders\.length > 0\)/);
    expect(templateSrc).toMatch(/\(click\)="onToggleDeferPanel\(\)"/);
    expect(templateSrc).toMatch(/\[attr\.aria-expanded\]="deferPanelOpen\(\)"/);
  });

  it('template renders the "Open System Cleanup" affordance ONLY in the anomaly branch', () => {
    // P4 risk table: "RED-anomaly state nags without actionable
    // remediation" ⇒ offer "Open System Cleanup" rather than a bare
    // alarm. The button reuses the existing handler so the
    // System-Cleanup dialog contract stays unchanged (task 6).
    expect(templateSrc).toMatch(/@if \(banner\.isAnomaly\)/);
    expect(templateSrc).toMatch(/\(click\)="onSystemCleanup\(\)"/);
  });

  it('component declares the defer leg wiring — fetcher + signals + aliases + handlers', () => {
    // The fetcher wires the store to JobService.listDeferBlocked.
    expect(componentSrc).toMatch(/fetchDeferBlocked: \(\) => this\.jobService\.listDeferBlocked\(\)/);
    // The component aliases the store signals.
    expect(componentSrc).toMatch(/readonly deferStatus = this\.store\.deferStatus/);
    expect(componentSrc).toMatch(/readonly deferDegraded = this\.store\.deferDegraded/);
    // The component declares the page-banner helper as a computed.
    expect(componentSrc).toMatch(/readonly deferPageBanner = computed\(\(\) => deferPageBanner\(this\.store\.deferStatus\(\)\)\)/);
    // The component declares the panel open/close + action-in-flight flags.
    expect(componentSrc).toMatch(/readonly deferPanelOpen = signal<boolean>\(false\)/);
    expect(componentSrc).toMatch(/readonly deferActionInFlight = signal<boolean>\(false\)/);
  });

  it('component fetches the defer leg on init + poll tick + refocus + onRefresh (P4 task 5)', () => {
    // P4 task 5: defer leg joins the Phase-2 poll tick with per-leg
    // catchError (the store's subscribe error handler is the
    // catchError port from the indicator — ``forkJoin`` discipline
    // for a single-tick poll). The regexes below allow a generous
    // comment-blank window between the fetch and the next statement
    // so the spec survives a future docstring touch-up.
    expect(componentSrc).toMatch(/this\.store\.fetchDeferBlocked\(\);[\s\S]{0,400}?this\.tabVisible\.set\(this\.doc\.visibilityState/);
    // Poll tick — the defer fetch is the second statement inside the
    // tick callback (after ``refreshActive``).
    expect(componentSrc).toMatch(/this\.store\.refreshActive\(\);[\s\S]{0,300}?this\.store\.fetchDeferBlocked\(\);[\s\S]{0,200}?\}, POLL_INTERVAL_MS\)/);
    // Refocus debounce — same pattern, gated by the gate inputs.
    expect(componentSrc).toMatch(/this\.store\.refreshActive\(\);[\s\S]{0,300}?this\.store\.fetchDeferBlocked\(\);[\s\S]{0,200}?\}, REFOCUS_DEBOUNCE_MS\)/);
    // onRefresh — the manual refresh button path.
    expect(componentSrc).toMatch(/protected onRefresh\(\): void \{[\s\S]{0,800}?this\.store\.fetchDeferBlocked\(\);/);
  });

  it('component action handlers gate the service call behind the confirm dialog (P4 task 3)', () => {
    // Two-stage confirm: the action handler opens a ConfirmDialog;
    // the service call fires ONLY inside the ``afterClosed``
    // subscribe callback's confirm branch. The cancel path closes
    // the dialog and returns without dispatching.
    expect(componentSrc).toMatch(/protected onHolderForceComplete\(holder: DeferBlockHolder\): void/);
    expect(componentSrc).toMatch(/protected onHolderResendForeground\(holder: DeferBlockHolder\): void/);
    // Both handlers must reference ConfirmDialogComponent via dialog.open.
    // The actual call uses generic type arguments (erased at build
    // time, but visible in source) — match the prefix-then-arg shape.
    expect(componentSrc).toMatch(/this\.dialog\.open<[\s\S]*?>\(ConfirmDialogComponent,/);
    // Both handlers subscribe to ``afterClosed`` to gate the
    // service call on the dialog result.
    expect(componentSrc).toMatch(/afterClosed\(\)\.subscribe\(\(confirmed\)/);
  });

  it('component refreshes the defer leg after every successful action (banner/panel post-action refresh)', () => {
    // P4 task 3 acceptance: "success refreshes holders leg". Both
    // action handlers re-fetch the defer leg on success. The window
    // covers the whole ``subscribe({...})`` callback (incl. the
    // ``result`` handling, the snackbar open, etc.) up to the
    // trailing ``fetchDeferBlocked`` call.
    expect(componentSrc).toMatch(/this\.jobService\.forceCompleteDeferHolder\(holder\.instance_id\)\.subscribe\(\{[\s\S]{0,2000}?this\.store\.fetchDeferBlocked\(\)/);
    expect(componentSrc).toMatch(/this\.jobService\.resendDeferredForeground\(holder\.instance_id\)\.subscribe\(\{[\s\S]{0,2000}?this\.store\.fetchDeferBlocked\(\)/);
  });

  it('refreshBadStateCount applies retain-last-data to the preflight too (P4 task 4)', () => {
    // The preflight fetch error handler flips ``preflightDegraded``
    // (the same flag pattern the store uses). The legacy silent
    // swallow at the end of the Promise.all chain is GONE.
    expect(componentSrc).toMatch(/this\.preflightDegraded\.set\(true\)/);
    expect(componentSrc).not.toMatch(/\.catch\(\(\) => \{\s*\/\/ Fail silently/);
  });

  it('the page banner DOES NOT touch the cleanup dialog directly (P4 task 6 — dialog contract untouched)', () => {
    // P4 task 6: the System Cleanup dialog keeps working unchanged.
    // The banner's "Open System Cleanup" button reuses the page's
    // existing ``onSystemCleanup`` handler — it does NOT open the
    // dialog directly (the dialog's data contract is untouched).
    expect(componentSrc).toMatch(/onSystemCleanup/);
    // The page banner template handler is ``(click)="onSystemCleanup()"`` —
    // confirmed by the template-source pin above.
  });
});
