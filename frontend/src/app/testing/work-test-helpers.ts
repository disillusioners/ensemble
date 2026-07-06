// Work test helpers — mirrors ``job-test-helpers.ts`` and
// ``queue-test-helpers.ts``. Keeps the spec files focused on
// behaviour rather than fixture scaffolding.
//
// The types here intentionally re-declare the Work model shape
// rather than importing from ``../models/work.model`` — the
// existing helpers do the same to dodge module-resolution issues
// in isolated test runs.

// WorkKind redefined here — the test helpers intentionally avoid
// importing from ``../models/work.model`` to dodge module-resolution
// issues in isolated test runs. Phase 4 partial collapse (2026-07-06)
// dropped ``'turn'`` — message turns are now JobItems.
export type WorkKind = 'job' | 'report';

export interface Work {
  work_id: string;
  kind: WorkKind;
  status: string;
  instance_id: string | null;
  project_id: string | null;
  agent_id: string | null;
  result_summary: string | null;
  error: string | null;
  created_at: string;
}

let workCounter = 0;

/**
 * Build a single Work fixture. Pass ``overrides`` to pin specific
 * fields (work_id, kind, status, project_id, etc.).
 */
export function createMockWork(overrides?: Partial<Work>): Work {
  workCounter += 1;
  const id = overrides?.work_id ?? `work-${workCounter}`;
  return {
    work_id: id,
    kind: 'job',
    status: 'pending',
    instance_id: null,
    project_id: 'project-123',
    agent_id: 'developer',
    result_summary: null,
    error: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

/**
 * Build a list of ``count`` Work fixtures with sequential
 * work_ids (``work-0``, ``work-1``, ...).
 */
export function createMockWorkList(count: number): Work[] {
  return Array.from({ length: count }, (_, i) =>
    createMockWork({ work_id: `work-${i}` })
  );
}