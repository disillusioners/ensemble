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
    const loop = templateSrc.match(/@for \(job of (\w+)\(\); track job\.job_id\)/);
    expect(loop).not.toBeNull();
    expect(loop![1]).toBe('displayedJobs');
    // Exactly one @for over the list, no second branch.
    expect(templateSrc.match(/@for \(job of /g)).toHaveLength(1);
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
});
